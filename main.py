"""Baixa os dados abertos do Infosiga, calcula o matrix profile e gera docs/index.html."""

import json
import re
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import stumpy

URL = "https://infosiga.detran.sp.gov.br/painel/download/file/dados_infosiga.zip"
ZIP_PATH = Path("data/dados_infosiga.zip")
DOCS = Path("docs")
INICIO = date(2022, 1, 1)
SERIES = ["fatais", "nao_fatais", "totais", "obitos"]
JANELAS = {"mensal": [3, 6, 12], "semanal": [4, 8, 13], "diaria": [7, 15, 30]}


def download(path: Path = ZIP_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        path.write_bytes(resp.read())
    if path.stat().st_size < 1_000_000 or not zipfile.is_zipfile(path):
        raise RuntimeError(f"Download inválido: {path} ({path.stat().st_size} bytes)")


def _read(z: zipfile.ZipFile, prefixo: str, colunas: list[str]) -> pl.DataFrame:
    nomes = [n for n in z.namelist() if re.search(rf"(^|/){prefixo}_.*\.csv$", n)]
    if not nomes:
        raise FileNotFoundError(f"Nenhum {prefixo}_*.csv no zip")
    return pl.concat(
        pl.read_csv(
            z.read(n), separator=";", encoding="latin1", columns=colunas, infer_schema=False
        )
        for n in nomes
    )


def load(path: Path = ZIP_PATH) -> pl.DataFrame:
    """Eventos com colunas (data, serie): um por sinistro fatal/não fatal e um por óbito."""
    with zipfile.ZipFile(path) as z:
        sin = _read(z, "sinistros", ["tipo_registro", "data_sinistro"])
        pes = _read(z, "pessoas", ["gravidade_lesao", "data_obito"])
    tipos = {"SINISTRO FATAL": "fatais", "SINISTRO NAO FATAL": "nao_fatais"}
    sin = sin.filter(pl.col("tipo_registro").is_in(list(tipos))).select(
        data=pl.col("data_sinistro"), serie=pl.col("tipo_registro").replace_strict(tipos)
    )
    obi = pes.filter(pl.col("gravidade_lesao") == "FATAL").select(
        data=pl.col("data_obito"), serie=pl.lit("obitos")
    )
    return pl.concat([sin, obi]).with_columns(
        pl.col("data").str.to_date("%d/%m/%Y", strict=False)
    ).drop_nulls("data")


def build_series(eventos: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Séries diária, semanal e mensal (colunas: data + SERIES), de INICIO ao último mês completo."""
    fim = eventos["data"].max()
    if (fim + timedelta(days=1)).day != 1:
        fim = fim.replace(day=1) - timedelta(days=1)
    calendario = pl.DataFrame({"data": pl.date_range(INICIO, fim, eager=True)})
    contagem = (
        eventos.filter(pl.col("data").is_between(INICIO, fim))
        .group_by("data", "serie")
        .len()
        .pivot("serie", index="data", values="len")
    )
    for s in ["fatais", "nao_fatais", "obitos"]:
        if s not in contagem.columns:
            contagem = contagem.with_columns(pl.lit(0, pl.UInt32).alias(s))
    diaria = (
        calendario.join(contagem, on="data", how="left")
        .fill_null(0)
        .with_columns(totais=pl.col("fatais") + pl.col("nao_fatais"))
        .select("data", *SERIES)
    )

    def agrega(every: str) -> pl.DataFrame:
        return (
            diaria.group_by(pl.col("data").dt.truncate(every))
            .agg(pl.col(SERIES).sum(), dias=pl.len())
            .sort("data")
        )

    semanal = agrega("1w").filter(pl.col("dias") == 7).drop("dias")
    mensal = agrega("1mo").drop("dias")
    return {"mensal": mensal, "semanal": semanal, "diaria": diaria}


def _lista(a: np.ndarray) -> list:
    return [round(float(v), 4) if np.isfinite(v) else None for v in a]


def matrix_profiles(series: dict[str, pl.DataFrame]) -> dict:
    out = {}
    for gran, df in series.items():
        out[gran] = {}
        for s in SERIES:
            y = df[s].to_numpy().astype(float)
            out[gran][s] = {}
            for m in JANELAS[gran]:
                mp = stumpy.stump(y, m)[:, 0].astype(float)
                ok = np.isfinite(mp)
                out[gran][s][m] = {
                    "mp": _lista(mp),
                    "discord": int(np.argmax(np.where(ok, mp, -np.inf))) if ok.any() else None,
                    "motif": int(np.argmin(np.where(ok, mp, np.inf))) if ok.any() else None,
                }
    return out


def render(series: dict[str, pl.DataFrame], mps: dict) -> None:
    DOCS.mkdir(exist_ok=True)
    diaria = series["diaria"]
    dados = {
        "gerado_em": date.today().isoformat(),
        "inicio": str(diaria["data"].min()),
        "fim": str(diaria["data"].max()),
        "janelas": JANELAS,
        "series": {
            g: {"x": [str(d) for d in df["data"]], **{s: df[s].to_list() for s in SERIES}}
            for g, df in series.items()
        },
        "mp": mps,
    }
    html = Path("template.html").read_text(encoding="utf-8")
    payload = json.dumps(dados, separators=(",", ":")).replace("</", "<\\/")
    (DOCS / "index.html").write_text(html.replace("__DATA__", payload), encoding="utf-8")

    linhas = []
    for g, df in series.items():
        datas = [str(d) for d in df["data"]]
        for s in SERIES:
            linhas += [(g, s, None, d, float(v)) for d, v in zip(datas, df[s])]
            for m, r in mps[g][s].items():
                linhas += [(g, s, m, d, v) for d, v in zip(datas, r["mp"])]
    pl.DataFrame(
        linhas,
        schema={
            "granularidade": pl.String,
            "serie": pl.String,
            "janela": pl.Int32,
            "data": pl.String,
            "valor": pl.Float64,
        },
        orient="row",
    ).write_csv(DOCS / "series.csv")


def main() -> None:
    if not ZIP_PATH.exists():
        download()
    series = build_series(load())
    render(series, matrix_profiles(series))
    print(f"docs/index.html gerado ({series['diaria']['data'].max()})")


if __name__ == "__main__":
    main()
