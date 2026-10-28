#!/usr/bin/env python3
"""
Gera a base de contas do Prospector Line a partir dos dados abertos do CNPJ (Receita Federal).

Mantém só empresas ATIVAS de porte "DEMAIS" (acima de EPP, ou seja, médias e grandes)
e agrega por empresa + município. Gera um arquivo por UF (UF.tsv.gz) e um meta.json.

Fonte: compartilhamento público da Receita (Nextcloud/WebDAV), atualizado mensalmente.
Uso: python gerar_base.py [--saida dados] [--mes AAAA-MM]
"""
import argparse, csv, gzip, io, json, os, re, sys, time, zipfile
from collections import defaultdict
from datetime import date
from xml.etree import ElementTree

import requests

# Fonte principal: espelho público da Casa dos Dados (mesmos arquivos da Receita, servidos por CDN, bem mais estável).
# Reserva: compartilhamento oficial da Receita (WebDAV), que costuma derrubar downloads longos.
ESPELHO = os.getenv("ESPELHO_URL", "https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos")
FONTE = os.getenv("FONTE", "espelho")  # "espelho" ou "receita"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) prospector-line-base/1.0"}
BASE = os.getenv("RF_WEBDAV", "https://arquivos.receitafederal.gov.br/public.php/webdav")
TOKEN = os.getenv("RF_TOKEN", "YggdBLfdninEJX9")
AUTH = (TOKEN, "")
MIN_CAPITAL = float(os.getenv("MIN_CAPITAL", "100000"))      # capital social mínimo...
MIN_ESTAB_SEM_CAPITAL = int(os.getenv("MIN_ESTAB", "3"))     # ...ou pelo menos N estabelecimentos ativos
DAV = {"d": "DAV:"}
csv.field_size_limit(10_000_000)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def propfind(path=""):
    url = f"{BASE}/{path}".rstrip("/") + "/"
    for tentativa in range(5):
        try:
            r = requests.request("PROPFIND", url, auth=AUTH, headers={"Depth": "1"}, timeout=(30, 120))
            r.raise_for_status()
            return ElementTree.fromstring(r.content)
        except Exception as e:
            log("PROPFIND falhou", url, e)
            time.sleep(10 * (tentativa + 1))
    raise SystemExit("Não consegui listar " + url)


def espelho_meses():
    r = requests.get(ESPELHO + "/", headers=UA, timeout=(30, 120))
    r.raise_for_status()
    return sorted(set(re.findall(r'href="(\d{4}-\d{2}(?:-\d{2})?)/"', r.text)))


def espelho_arquivos(mes):
    r = requests.get(f"{ESPELHO}/{mes}/", headers=UA, timeout=(30, 120))
    r.raise_for_status()
    return sorted(set(re.findall(r'href="([^"/]+\.zip)"', r.text, re.I)))


def meses_disponiveis():
    root = propfind()
    meses = []
    for resp in root.findall("d:response", DAV):
        m = re.search(r"(\d{4}-\d{2})/?$", resp.find("d:href", DAV).text or "")
        if m:
            meses.append(m.group(1))
    return sorted(meses)


def arquivos_do_mes(mes):
    root = propfind(mes)
    out = []
    for resp in root.findall("d:response", DAV):
        m = re.search(r"/([^/]+\.zip)$", resp.find("d:href", DAV).text or "", re.I)
        if m:
            out.append(m.group(1))
    return sorted(out)


def baixar(mes, nome, destino):
    """Download com retomada (Range), novas tentativas e espera crescente: os servidores oscilam em arquivos grandes."""
    if FONTE == "espelho":
        url, auth = f"{ESPELHO}/{mes}/{nome}", None
    else:
        url, auth = f"{BASE}/{mes}/{nome}", AUTH
    falhas_sem_progresso = 0
    for tentativa in range(40):
        ja = os.path.getsize(destino) if os.path.exists(destino) else 0
        headers = dict(UA)
        if ja:
            headers["Range"] = f"bytes={ja}-"
        try:
            with requests.get(url, auth=auth, headers=headers, stream=True, timeout=(30, 180)) as r:
                if r.status_code == 416:
                    return destino
                if ja and r.status_code != 206:
                    ja = 0
                r.raise_for_status()
                with open(destino, "ab" if ja else "wb") as f:
                    for bloco in r.iter_content(1 << 20):
                        f.write(bloco)
            zipfile.ZipFile(destino).close()
            log(f"{nome}: {os.path.getsize(destino) / 1e6:.0f} MB baixados")
            return destino
        except zipfile.BadZipFile:
            log("zip incompleto, retomando", nome)
        except Exception as e:
            novo = os.path.getsize(destino) if os.path.exists(destino) else 0
            falhas_sem_progresso = 0 if novo > ja else falhas_sem_progresso + 1
            espera = min(300, 15 * (falhas_sem_progresso + 1))
            log(f"download {nome} falhou ({e}); {novo / 1e6:.0f} MB até agora; nova tentativa em {espera}s")
            time.sleep(espera)
    raise SystemExit("Falha definitiva ao baixar " + nome)


def linhas_zip(caminho):
    with zipfile.ZipFile(caminho) as z:
        for info in z.infolist():
            with z.open(info) as f:
                yield from csv.reader(io.TextIOWrapper(f, encoding="latin-1", newline=""), delimiter=";", quotechar='"')


def capital(v):
    try:
        return float((v or "0").replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


# ---------- Radar de gatilhos ----------
# Unidade nova = filial ATIVA com data de início de atividade nos últimos 12 meses antes da data da base.
# Sai na mesma rodada, sem depender da base anterior.
MAX_NOVAS_LISTADAS = 5


def data_ref(mes, hoje=None):
    """Data de referência da base: 'AAAA-MM-DD' ou 'AAAA-MM' (dia 1). Sem mês válido, usa hoje."""
    m = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$", mes or "")
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
        except ValueError:
            pass
    return hoje or date.today()


def menos_meses(d, n):
    """Mesma data n meses antes; dia ajustado para o fim do mês quando não existe (31/03 - 1 mês = 28 ou 29/02)."""
    a, m = divmod(d.year * 12 + d.month - 1 - n, 12)
    m += 1
    for dia in (d.day, 30, 29, 28):
        try:
            return date(a, m, dia)
        except ValueError:
            continue


def iso8(d):
    return d.strftime("%Y%m%d")


class Radar:
    """Acumula, por empresa, as filiais abertas na janela e a data de abertura da matriz."""

    def __init__(self, ref):
        self.ref = iso8(ref)
        self.l12 = iso8(menos_meses(ref, 12))
        self.l6 = iso8(menos_meses(ref, 6))
        self.novas = {}       # basico -> [qtd12, qtd6, [(data, municipio), ...]]
        self.matriz = {}      # basico -> data de abertura da matriz

    def registrar(self, basico, matriz_filial, inicio, municipio):
        inicio = (inicio or "").strip()
        if not re.fullmatch(r"\d{8}", inicio):
            return
        if matriz_filial == "1":
            self.matriz[basico] = inicio
            return
        if not (self.l12 <= inicio <= self.ref):   # fora da janela ou data futura (erro de cadastro)
            return
        g = self.novas.get(basico)
        if g is None:
            g = self.novas[basico] = [0, 0, []]
        g[0] += 1
        if inicio >= self.l6:
            g[1] += 1
        g[2].append((inicio, municipio))
        if len(g[2]) > MAX_NOVAS_LISTADAS * 4:   # limita memória em empresas com centenas de unidades novas
            g[2] = sorted(g[2], reverse=True)[:MAX_NOVAS_LISTADAS]

    def colunas(self, basico):
        """[unid_novas_12m, unid_novas_6m, novas_recentes, abertura_matriz] para a linha de saída."""
        g = self.novas.get(basico)
        if not g:
            return ["0", "0", "", self.matriz.get(basico, "")]
        recentes = ",".join(f"{mun}:{d}" for d, mun in sorted(g[2], reverse=True)[:MAX_NOVAS_LISTADAS])
        return [str(g[0]), str(g[1]), recentes, self.matriz.get(basico, "")]


def processar(arquivos_empresas, arquivos_estab, saida, mes):
    # 1) Empresas de porte DEMAIS (05)
    empresas = {}
    for arq in arquivos_empresas:
        n = 0
        for c in linhas_zip(arq):
            if len(c) >= 6 and c[5] == "05":
                empresas[c[0]] = (c[1].strip(), c[2], capital(c[4]))
                n += 1
        log(f"{os.path.basename(arq)}: {n} empresas porte DEMAIS")
    log("Total de empresas DEMAIS:", len(empresas))

    # 2) Estabelecimentos ativos dessas empresas, agregados por empresa + município
    total_ativos = defaultdict(int)
    radar = Radar(data_ref(mes))
    grupos = {}  # (basico, municipio) -> [qtd, linha representativa]
    for arq in arquivos_estab:
        n = 0
        for c in linhas_zip(arq):
            if len(c) < 30 or c[5] != "02" or c[0] not in empresas:
                continue
            total_ativos[c[0]] += 1
            radar.registrar(c[0], c[3], c[10], c[20])
            chave = (c[0], c[20])
            g = grupos.get(chave)
            rep = (c[1], c[2], c[3], c[4].strip(), c[11], c[19], c[20], c[21] + c[22], c[27].strip().lower(), c[10], c[17].strip(), c[18])
            if g is None:
                grupos[chave] = [1, rep]
            else:
                g[0] += 1
                if rep[2] == "1" and g[1][2] != "1":  # prefere a matriz como representante
                    g[1] = rep
            n += 1
        log(f"{os.path.basename(arq)}: {n} estabelecimentos ativos de empresas DEMAIS")

    # 3) Saída por UF
    os.makedirs(saida, exist_ok=True)
    por_uf = defaultdict(list)
    for (basico, mun), (qtd, rep) in grupos.items():
        razao, natureza, cap = empresas[basico]
        tot = total_ativos[basico]
        ordem, dv, mf, fantasia, cnae, uf, municipio, tel, email, abertura, bairro, cep = rep
        # Hospitais filantrópicos têm capital zero e às vezes uma unidade só; entram mesmo assim.
        if cap < MIN_CAPITAL and tot < MIN_ESTAB_SEM_CAPITAL and not cnae.startswith("861"):
            continue
        por_uf[uf].append([basico + ordem + dv, mf, razao, fantasia, cnae, municipio, uf, f"{cap:.0f}", natureza,
                           str(tot), str(qtd), tel, email, abertura, bairro, cep] + radar.colunas(basico))
    cab = ["cnpj", "matriz_filial", "razao_social", "nome_fantasia", "cnae", "municipio_rf", "uf", "capital_social",
           "natureza_juridica", "estab_ativos_brasil", "estab_no_municipio", "telefone", "email", "abertura", "bairro", "cep",
           "unid_novas_12m", "unid_novas_6m", "novas_recentes", "abertura_matriz"]
    resumo = {}
    for uf, linhas in por_uf.items():
        if not uf or len(uf) != 2 or uf == "EX":
            continue
        linhas.sort(key=lambda l: -int(l[9]))
        caminho = os.path.join(saida, f"{uf}.tsv.gz")
        with gzip.open(caminho, "wt", encoding="utf-8", newline="") as f:
            f.write("\t".join(cab) + "\n")
            for l in linhas:
                f.write("\t".join(x.replace("\t", " ").replace("\n", " ") for x in l) + "\n")
        resumo[uf] = {"linhas": len(linhas), "bytes": os.path.getsize(caminho)}
        log(f"{uf}: {len(linhas)} linhas, {os.path.getsize(caminho) / 1e6:.1f} MB")
    meta = {"mes_receita": mes, "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "criterio": {"porte": "DEMAIS", "situacao": "ATIVA", "min_capital": MIN_CAPITAL, "min_estab": MIN_ESTAB_SEM_CAPITAL},
            "radar": {"referencia": radar.ref, "desde_12m": radar.l12, "desde_6m": radar.l6,
                      "empresas_com_unidade_nova": len(radar.novas)},
            "colunas": cab, "ufs": resumo}
    with open(os.path.join(saida, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", default="dados")
    ap.add_argument("--mes", default="")
    ap.add_argument("--tmp", default="tmp_rf")
    a = ap.parse_args()
    global FONTE
    try:
        if FONTE != "espelho":
            raise RuntimeError("fonte receita escolhida")
        meses = espelho_meses()
        mes = a.mes if a.mes in meses else meses[-1]
        arqs = espelho_arquivos(mes)
        log("Fonte: espelho Casa dos Dados")
    except Exception as e:
        log("Usando o servidor da Receita:", e)
        FONTE = "receita"
        mes = a.mes or meses_disponiveis()[-1]
        arqs = arquivos_do_mes(mes)
    log("Mês da base:", mes)
    emp = [x for x in arqs if x.lower().startswith("empresas")]
    est = [x for x in arqs if x.lower().startswith("estabelecimentos")]
    if not emp or not est:
        raise SystemExit(f"Arquivos não encontrados em {mes}: {arqs}")
    os.makedirs(a.tmp, exist_ok=True)
    # Empresas: baixa todos (são menores); Estabelecimentos: baixa, processa e apaga um por vez
    caminhos_emp = [baixar(mes, x, os.path.join(a.tmp, x)) for x in emp]

    def estab_iter():
        for x in est:
            p = baixar(mes, x, os.path.join(a.tmp, x))
            yield p
            os.remove(p)

    meta = processar(caminhos_emp, estab_iter(), a.saida, mes)
    cn = [x for x in arqs if x.lower().startswith("cnae")]
    if cn:
        p = baixar(mes, cn[0], os.path.join(a.tmp, cn[0]))
        cnaes = {c[0]: c[1].strip() for c in linhas_zip(p) if len(c) >= 2}
        with open(os.path.join(a.saida, "cnaes.json"), "w", encoding="utf-8") as f:
            json.dump(cnaes, f, ensure_ascii=False)
        log("CNAEs:", len(cnaes))
    log("Concluído:", meta["ufs"].keys())


if __name__ == "__main__":
    main()
