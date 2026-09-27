import io
import re
import zipfile
import datetime
import xml.etree.ElementTree as ET
import pandas as pd
from app import db
from app.feeds import common

# monthly average daily rate of tourist accommodation by nuts 2 region and accommodation type, published by turismo de portugal from the ine guest survey
PAGE_URL = "https://travelbi.turismodeportugal.pt/en/accommodation/revpar-and-adr/"
months_pt = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
regions = ["Norte", "Centro", "Oeste e Vale do Tejo", "Grande Lisboa", "A.M. Lisboa", "Área Metropolitana de Lisboa", "Península de Setúbal", "Alentejo", "Algarve", "Açores", "Madeira", "Total Global"]
typologies = ["Hotéis", "5*", "4*", "3*", "2* e 1*", "Hotéis-Apartamentos", "Pousadas", "Pousadas*", "Aldeamentos Turísticos", "Apartamentos Turísticos", "Alojamento Local", "Alojamento Local**", "Turismo no Espaço Rural"]
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


# the download links of the series archives on the report page, newest classification first
def archive_links():
    html = common.get_text(PAGE_URL)
    links = sorted(set(re.findall(r"https?://travelbi\.turismodeportugal\.pt/api\.sharepoint/download\?fileUrl=[^\"' ]+", html)))
    return links


# rows of every sheet of a workbook as dicts keyed by column letter, shared strings resolved
def read_workbook(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(NS + "si"):
            shared.append("".join(t.text or "" for t in si.iter(NS + "t")))
    sheets = []
    for name in sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet")):
        rows = []
        for row in ET.fromstring(z.read(name)).iter(NS + "row"):
            cells = {}
            for c in row.findall(NS + "c"):
                ref = re.match(r"([A-Z]+)", c.get("r")).group(1)
                v = c.find(NS + "v")
                val = v.text if v is not None else ""
                if c.get("t") == "s":
                    val = shared[int(val)]
                cells[ref] = val
            rows.append(cells)
        sheets.append(rows)
    return sheets


# parse the adr workbook of one archive: region blocks with the typology rows below each region, one sheet per year
def parse_adr(data, source):
    rows = []
    for sheet in read_workbook(data):
        year = None
        block = None
        region = None
        for r in sheet:
            a = r.get("A", "").strip()
            b = r.get("B", "").strip()
            if a == "" and re.fullmatch(r"\d{4}(\.0)?", b):
                year = int(float(b))
                continue
            if a.startswith("ADR"):
                block = a
                region = None
                continue
            if year is None or block is None:
                continue
            if a in regions:
                region = a
                typology = "Total"
            elif a in typologies and region is not None and "Tipologia" in block:
                typology = a if a in ("5*", "4*", "3*", "2* e 1*") else a.rstrip("*")
            else:
                continue
            if "Portugal" in block:
                region = "Portugal"
            for i, col in enumerate("BCDEFGHIJKLM"):
                v = r.get(col, "")
                if re.fullmatch(r"[0-9]+(\.[0-9]+)?", v or ""):
                    rows.append({"region": region, "typology": typology, "month": datetime.date(year, i + 1, 1), "adr": float(v), "source": source, "fetched_at": common.now_utc()})
    return rows


# download the archives, parse the adr workbooks and replace the table; the newest classification wins where years overlap
def refresh():
    links = archive_links()
    if len(links) == 0:
        raise RuntimeError("no series archives found on the travelbi page")
    frames = []
    for link in links:
        data = common.get_bytes(link)
        archive = zipfile.ZipFile(io.BytesIO(data))
        adr_files = [n for n in archive.namelist() if n.endswith("adr.xlsx")]
        if len(adr_files) == 0:
            continue
        source = adr_files[0].split("/")[0]
        frames.append(pd.DataFrame(parse_adr(archive.read(adr_files[0]), source)))
    frame = pd.concat(frames, ignore_index=True)
    frame["region"] = frame["region"].replace({"A.M. Lisboa": "Grande Lisboa", "Área Metropolitana de Lisboa": "Grande Lisboa"})
    frame["rank"] = frame["source"].str.extract(r"(\d{4})").astype(int)
    frame = frame.sort_values("rank", ascending=False).drop_duplicates(["region", "typology", "month"]).drop(columns="rank")
    with db.db_lock:
        con = db.connect()
        try:
            con.execute("create table if not exists raw.adr_regional (region varchar, typology varchar, month date, adr double, source varchar, fetched_at timestamp)")
            con.register("incoming", frame)
            con.execute("delete from raw.adr_regional")
            con.execute("insert into raw.adr_regional select region, typology, month, adr, source, fetched_at from incoming")
        finally:
            con.close()
    return len(frame)
