"""Testes do Radar de gatilhos do gerar_base.py. Rodar: python3 -m unittest discover -s tests -v"""
import csv, gzip, io, os, sys, tempfile, unittest, zipfile
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import gerar_base as gb


class DataRef(unittest.TestCase):
    def test_aceita_mes_com_e_sem_dia(self):
        self.assertEqual(gb.data_ref("2026-09-14"), date(2026, 9, 14))
        self.assertEqual(gb.data_ref("2026-09"), date(2026, 9, 1))

    def test_mes_invalido_cai_para_hoje(self):
        self.assertEqual(gb.data_ref("lixo", hoje=date(2026, 1, 2)), date(2026, 1, 2))
        self.assertEqual(gb.data_ref("2026-13", hoje=date(2026, 1, 2)), date(2026, 1, 2))

    def test_menos_meses_ajusta_fim_de_mes(self):
        self.assertEqual(gb.menos_meses(date(2026, 9, 14), 12), date(2025, 9, 14))
        self.assertEqual(gb.menos_meses(date(2026, 3, 31), 1), date(2026, 2, 28))
        self.assertEqual(gb.menos_meses(date(2026, 1, 15), 6), date(2025, 7, 15))


class Radar(unittest.TestCase):
    def setUp(self):
        self.r = gb.Radar(date(2026, 9, 14))

    def test_conta_filiais_na_janela_de_12_e_6_meses(self):
        self.r.registrar("A", "2", "20260801", "7107")   # 6m e 12m
        self.r.registrar("A", "2", "20251001", "6619")   # só 12m
        self.r.registrar("A", "2", "20250913", "6001")   # um dia antes da janela: fora
        self.assertEqual(self.r.colunas("A")[:2], ["2", "1"])

    def test_limite_da_janela_entra(self):
        self.r.registrar("A", "2", "20250914", "1")
        self.r.registrar("A", "2", "20260914", "2")
        self.assertEqual(self.r.colunas("A")[0], "2")

    def test_matriz_nova_nao_conta_como_unidade_nova_mas_fica_registrada(self):
        self.r.registrar("A", "1", "20260801", "7107")
        self.assertEqual(self.r.colunas("A"), ["0", "0", "", "20260801"])

    def test_ignora_data_futura_e_invalida(self):
        for d in ("20270101", "", "2026-08-01", "abc"):
            self.r.registrar("A", "2", d, "1")
        self.assertEqual(self.r.colunas("A")[0], "0")

    def test_lista_as_5_mais_recentes_em_ordem(self):
        for i in range(1, 9):
            self.r.registrar("A", "2", f"202608{i:02d}", str(i))
        q12, _, recentes, _ = self.r.colunas("A")
        self.assertEqual(q12, "8")
        self.assertEqual(recentes, "8:20260808,7:20260807,6:20260806,5:20260805,4:20260804")

    def test_memoria_limitada_sem_perder_contagem_nem_as_mais_recentes(self):
        for i in range(300):
            self.r.registrar("A", "2", "20260101" if i % 2 else "20260601", str(i))
        q12, q6, recentes, _ = self.r.colunas("A")
        self.assertEqual((q12, q6), ("300", "150"))
        self.assertTrue(all(x.endswith(":20260601") for x in recentes.split(",")))
        self.assertLessEqual(len(self.r.novas["A"][2]), gb.MAX_NOVAS_LISTADAS * 4)

    def test_empresa_sem_nada(self):
        self.assertEqual(self.r.colunas("Z"), ["0", "0", "", ""])


def zip_csv(caminho, nome, linhas):
    buf = io.StringIO()
    csv.writer(buf, delimiter=";", quotechar='"', quoting=csv.QUOTE_ALL).writerows(linhas)
    with zipfile.ZipFile(caminho, "w") as z:
        z.writestr(nome, buf.getvalue().encode("latin-1"))


def estab(basico, ordem, mf, sit, inicio, uf, mun):
    c = [""] * 30
    c[0], c[1], c[2], c[3], c[4], c[5], c[10], c[11], c[19], c[20] = basico, ordem, "00", mf, "X", sit, inicio, "4751201", uf, mun
    return c


class PonteAPonta(unittest.TestCase):
    """Roda processar() com arquivos no formato da Receita e confere o TSV gerado."""

    def test_colunas_do_radar_no_tsv_e_no_meta(self):
        with tempfile.TemporaryDirectory() as d:
            emp, est = os.path.join(d, "Empresas0.zip"), os.path.join(d, "Estabelecimentos0.zip")
            zip_csv(emp, "EMPRECSV", [["11111111", "REDE X S.A.", "2046", "10", "5000000,00", "05", ""],
                                      ["22222222", "PADARIA", "2062", "49", "1000,00", "01", ""]])
            zip_csv(est, "ESTABELE", [
                estab("11111111", "0001", "1", "02", "19990101", "SP", "7107"),
                estab("11111111", "0002", "2", "02", "20260701", "SP", "6619"),   # nova
                estab("11111111", "0003", "2", "02", "20251101", "SP", "6619"),   # nova, só 12m
                estab("11111111", "0004", "2", "02", "20200101", "SP", "7107"),   # antiga
                estab("11111111", "0005", "2", "08", "20260801", "SP", "7107"),   # baixada: não conta
                estab("22222222", "0001", "1", "02", "20260801", "SP", "7107"),   # porte 01: fora da base
            ])
            meta = gb.processar([emp], iter([est]), os.path.join(d, "out"), "2026-09-14")
            with gzip.open(os.path.join(d, "out", "SP.tsv.gz"), "rt", encoding="utf-8") as f:
                linhas = list(csv.DictReader(f, delimiter="\t"))
        self.assertEqual(len(linhas), 2)   # 11111111 em 7107 e em 6619
        for l in linhas:
            self.assertEqual((l["unid_novas_12m"], l["unid_novas_6m"]), ("2", "1"))
            self.assertEqual(l["novas_recentes"], "6619:20260701,6619:20251101")
            self.assertEqual(l["abertura_matriz"], "19990101")
        self.assertEqual(meta["radar"], {"referencia": "20260914", "desde_12m": "20250914", "desde_6m": "20260314",
                                         "empresas_com_unidade_nova": 1})
        self.assertEqual(meta["colunas"][-4:], ["unid_novas_12m", "unid_novas_6m", "novas_recentes", "abertura_matriz"])


if __name__ == "__main__":
    unittest.main()
