import io
import zipfile
from datetime import date

import polars as pl

import main


def test_series(tmp_path):
    sin = (
        "tipo_registro;data_sinistro\n"
        "SINISTRO FATAL;01/01/2022\n"
        "SINISTRO NAO FATAL;01/01/2022\n"
        "NOTIFICACAO;01/01/2022\n"
        "SINISTRO NAO FATAL;10/02/2022\n"
        "SINISTRO FATAL;31/12/2021\n"  # antes de INICIO
        "SINISTRO NAO FATAL;05/03/2022\n"  # mês incompleto: descartado
    )
    pes = "gravidade_lesao;data_obito\nFATAL;02/01/2022\nLEVE;\nFATAL;28/02/2022\n"
    zp = tmp_path / "d.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("dados_infosiga/sinistros_2022-2024.csv", sin.encode("latin1"))
        z.writestr("dados_infosiga/pessoas_2022-2024.csv", pes.encode("latin1"))

    s = main.build_series(main.load(zp))
    d, w, m = s["diaria"], s["semanal"], s["mensal"]

    assert d["data"].min() == date(2022, 1, 1) and d["data"].max() == date(2022, 2, 28)
    assert d.height == 59  # dias sem evento preenchidos com 0
    assert d.select(main.SERIES).sum().row(0) == (1, 2, 3, 2)  # NOTIFICACAO fora
    assert m.select(main.SERIES).rows() == [(1, 1, 2, 1), (0, 1, 1, 1)]
    # semanas parciais descartadas: 2022-01-03 (seg) a 2022-02-27 (dom) = 8 semanas
    assert w["data"].min() == date(2022, 1, 3) and w.height == 8
    assert w["nao_fatais"].sum() == 1  # 01/01 caiu em semana parcial


def test_mp_e_render(tmp_path, monkeypatch):
    serie = pl.DataFrame({"data": pl.date_range(date(2022, 1, 1), date(2022, 6, 30), eager=True)})
    serie = serie.with_columns(**{c: pl.int_range(serie.height) ** 2 % 11 for c in main.SERIES})
    series = {"mensal": serie, "semanal": serie, "diaria": serie}
    mps = main.matrix_profiles(series)
    assert len(mps["mensal"]["obitos"][12]["mp"]) == serie.height - 12 + 1

    (tmp_path / "template.html").write_text("<script>const D=__DATA__</script>")
    monkeypatch.chdir(tmp_path)
    main.render(series, mps)
    assert "__DATA__" not in (tmp_path / "docs/index.html").read_text()
    assert pl.read_csv(tmp_path / "docs/series.csv").height > 0
