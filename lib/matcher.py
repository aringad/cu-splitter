"""
Matching tra CU estratte e anagrafica dipendenti.

Supporta match esatto su codice fiscale e fuzzy su cognome+nome.
Carica CSV (auto-detect separatore) e Excel.
"""

import csv
import io
import unicodedata
from dataclasses import dataclass
from enum import Enum

import pandas as pd
from thefuzz import fuzz

from lib.pdf_parser import CURecord


class MatchStatus(str, Enum):
    MATCHED = "matched"
    CU_UNMATCHED = "cu_unmatched"
    ANAGRAFICA_UNMATCHED = "anagrafica_unmatched"


@dataclass
class AnagraficaRecord:
    """Un record dall'anagrafica dipendenti."""
    cognome: str
    nome: str
    codice_fiscale: str
    email: str

    @property
    def full_name_normalized(self) -> str:
        return _normalize(f"{self.cognome} {self.nome}")


@dataclass
class MatchResult:
    """Risultato del matching di una CU con l'anagrafica."""
    cu: CURecord
    anagrafica: AnagraficaRecord | None
    status: MatchStatus
    email: str = ""
    match_score: int = 0
    match_method: str = ""


def _normalize(text: str) -> str:
    """Normalizza testo per confronto: uppercase, rimuovi accenti, strip."""
    text = text.upper().strip()
    # Rimuovi accenti
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def load_anagrafica(file_bytes: bytes, filename: str) -> list[AnagraficaRecord]:
    """
    Carica anagrafica da CSV o Excel.
    Auto-detect del separatore per CSV.

    Colonne attese (case-insensitive, flessibili):
        cognome, nome, codice_fiscale (o cf), email
    """
    filename_lower = filename.lower()

    if filename_lower.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(file_bytes))
    else:
        # CSV: auto-detect separatore
        text = file_bytes.decode("utf-8-sig")  # gestisce BOM
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
            sep = dialect.delimiter
        except csv.Error:
            sep = ";"  # fallback comune in Italia
        df = pd.read_csv(io.StringIO(text), sep=sep)

    # Normalizza nomi colonne
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Mappa colonne flessibili
    col_map = {}
    for col in df.columns:
        if "cognome" in col or "denominazione" in col:
            col_map["cognome"] = col
        elif "nome" in col and "cognome" not in col:
            col_map["nome"] = col
        elif col in ("cf", "codice_fiscale", "codicefiscale", "cod_fiscale"):
            col_map["codice_fiscale"] = col
        elif "fiscale" in col:
            col_map["codice_fiscale"] = col
        elif "email" in col or "mail" in col or "e-mail" in col:
            col_map["email"] = col

    records = []
    for _, row in df.iterrows():
        cognome = str(row.get(col_map.get("cognome", "cognome"), "")).strip()
        nome = str(row.get(col_map.get("nome", "nome"), "")).strip()
        cf = str(row.get(col_map.get("codice_fiscale", "codice_fiscale"), "")).strip().upper()
        email = str(row.get(col_map.get("email", "email"), "")).strip()

        # Ignora righe vuote
        if cf == "NAN":
            cf = ""
        if email == "NAN":
            email = ""
        if cognome == "NAN":
            cognome = ""
        if nome == "NAN":
            nome = ""

        if cognome or nome or cf:
            records.append(AnagraficaRecord(
                cognome=cognome.upper(),
                nome=nome.upper(),
                codice_fiscale=cf,
                email=email,
            ))

    return records


def match_cu_with_anagrafica(
    cu_records: list[CURecord],
    anagrafica: list[AnagraficaRecord],
    fuzzy_threshold: int = 80,
) -> list[MatchResult]:
    """
    Esegue il matching tra CU e anagrafica.

    Match primario: codice fiscale esatto (case-insensitive).
    Match secondario: fuzzy su cognome+nome (soglia configurabile).

    Returns:
        Lista di MatchResult per ogni CU,
        più MatchResult per anagrafiche senza CU.
    """
    results = []
    matched_anagrafica_indices = set()

    # Indice per CF
    cf_index: dict[str, int] = {}
    for idx, a in enumerate(anagrafica):
        if a.codice_fiscale:
            cf_index[a.codice_fiscale.upper()] = idx

    for cu in cu_records:
        best_match: AnagraficaRecord | None = None
        best_score = 0
        best_method = ""
        best_idx = -1

        # Match primario: CF esatto
        cu_cf = cu.codice_fiscale.upper() if cu.codice_fiscale else ""
        if cu_cf and cu_cf in cf_index:
            idx = cf_index[cu_cf]
            best_match = anagrafica[idx]
            best_score = 100
            best_method = "Codice Fiscale"
            best_idx = idx
        else:
            # Match secondario: fuzzy su cognome+nome
            cu_name = _normalize(f"{cu.cognome} {cu.nome}")
            if cu_name.strip():
                for idx, a in enumerate(anagrafica):
                    if idx in matched_anagrafica_indices:
                        continue
                    a_name = a.full_name_normalized
                    score = fuzz.token_sort_ratio(cu_name, a_name)
                    if score > best_score:
                        best_score = score
                        best_match = a
                        best_idx = idx
                        best_method = "Nome (fuzzy)"

                if best_score < fuzzy_threshold:
                    best_match = None
                    best_score = 0
                    best_method = ""
                    best_idx = -1

        if best_match and best_idx >= 0:
            matched_anagrafica_indices.add(best_idx)
            results.append(MatchResult(
                cu=cu,
                anagrafica=best_match,
                status=MatchStatus.MATCHED,
                email=best_match.email,
                match_score=best_score,
                match_method=best_method,
            ))
        else:
            results.append(MatchResult(
                cu=cu,
                anagrafica=None,
                status=MatchStatus.CU_UNMATCHED,
            ))

    # Aggiungi anagrafiche senza CU
    for idx, a in enumerate(anagrafica):
        if idx not in matched_anagrafica_indices:
            # Crea un CURecord fittizio per le anagrafiche senza CU
            results.append(MatchResult(
                cu=CURecord(
                    index=-1,
                    start_page=-1,
                    end_page=-1,
                    anno="",
                    codice_fiscale=a.codice_fiscale,
                    cognome=a.cognome,
                    nome=a.nome,
                ),
                anagrafica=a,
                status=MatchStatus.ANAGRAFICA_UNMATCHED,
                email=a.email,
            ))

    return results
