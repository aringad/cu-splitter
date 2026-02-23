"""
Parsing e split di PDF contenenti Certificazioni Uniche (CU).

Identifica i confini di ogni singola CU nel PDF massivo,
estrae i dati del percipiente (cognome, nome, codice fiscale)
e permette l'export in PDF singoli.
"""

import re
import io
import zipfile
from dataclasses import dataclass, field

import fitz  # PyMuPDF


# Pattern per identificare l'inizio di una nuova CU
CU_START_PATTERN = re.compile(
    r"CERTIFICAZIONE\s+UNICA\s+(\d{4})", re.IGNORECASE
)

# Codice fiscale italiano: 6 lettere + 2 cifre + 1 lettera + 2 cifre + 1 lettera + 3 cifre + 1 lettera
CF_PATTERN = re.compile(r"\b([A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z])\b")

# Pattern per identificare la sezione dati del percipiente
PERCIPIENTE_SECTION_PATTERNS = [
    re.compile(r"DATI\s+RELATIVI\s+AL\s+DIPENDENTE", re.IGNORECASE),
    re.compile(r"DATI\s+ANAGRAFICI\s+DEL\s+PERCIPIENTE", re.IGNORECASE),
    re.compile(r"DATI\s+RELATIVI\s+AL\s+PERCIPIENTE", re.IGNORECASE),
    re.compile(r"DATI\s+ANAGRAFICI", re.IGNORECASE),
]

# Pattern per cognome e nome nella sezione percipiente
COGNOME_PATTERN = re.compile(r"Cognome\s+o\s+Denominazione\s*[:\s]*([A-Z\s'À-Ú]+)", re.IGNORECASE)
NOME_PATTERN = re.compile(r"(?<!\bCognome\b\s{0,5})Nome\s*[:\s]*([A-Z\s'À-Ú]+)", re.IGNORECASE)


@dataclass
class CURecord:
    """Dati estratti da una singola Certificazione Unica."""
    index: int
    start_page: int  # 0-based
    end_page: int     # 0-based, inclusive
    anno: str
    codice_fiscale: str = ""
    cognome: str = ""
    nome: str = ""
    raw_text: str = field(default="", repr=False)

    @property
    def filename(self) -> str:
        """Nome file per l'export: CU2025_Cognome_Nome_CF.pdf"""
        cognome = self.cognome.strip().replace(" ", "").title() if self.cognome else "Sconosciuto"
        nome = self.nome.strip().replace(" ", "").title() if self.nome else "Sconosciuto"
        cf = self.codice_fiscale.upper() if self.codice_fiscale else "CFMANCANTE"
        return f"CU{self.anno}_{cognome}_{nome}_{cf}.pdf"


def _find_cu_boundaries(doc: fitz.Document) -> list[tuple[int, str]]:
    """
    Scansiona ogni pagina e trova dove inizia una nuova CU.
    Restituisce lista di (page_index, anno).
    """
    boundaries = []
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        text = page.get_text("text")
        match = CU_START_PATTERN.search(text)
        if match:
            # Verifica che sia davvero l'inizio (prima pagina della CU)
            # e non un riferimento a "CERTIFICAZIONE UNICA" dentro il testo
            # L'inizio è tipicamente nella parte alta della pagina
            anno = match.group(1)
            # Controlla posizione verticale del match nel testo
            # Se "CERTIFICAZIONE UNICA" appare nei primi 1/3 del testo, è un header
            match_pos = match.start()
            if match_pos < len(text) * 0.5:
                boundaries.append((page_idx, anno))
    return boundaries


def _extract_percipiente_cf(text: str) -> str:
    """
    Estrae il codice fiscale del percipiente dal testo della CU.
    Il CF del percipiente è tipicamente il secondo CF trovato
    (il primo è quello del sostituto d'imposta),
    oppure quello che segue la sezione "DATI RELATIVI AL DIPENDENTE".
    """
    # Strategia 1: cercare CF dopo la sezione percipiente
    for pattern in PERCIPIENTE_SECTION_PATTERNS:
        section_match = pattern.search(text)
        if section_match:
            after_section = text[section_match.end():]
            cf_match = CF_PATTERN.search(after_section)
            if cf_match:
                return cf_match.group(1)

    # Strategia 2: prendere il secondo CF trovato nel testo
    all_cfs = CF_PATTERN.findall(text)
    if len(all_cfs) >= 2:
        return all_cfs[1]
    elif len(all_cfs) == 1:
        return all_cfs[0]

    return ""


def _extract_nome_cognome(text: str) -> tuple[str, str]:
    """
    Estrae cognome e nome del percipiente dal testo della CU.
    Cerca nella sezione dati del percipiente.
    """
    cognome = ""
    nome = ""

    # Trova la sezione del percipiente
    section_start = 0
    for pattern in PERCIPIENTE_SECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            section_start = match.end()
            break

    search_text = text[section_start:] if section_start > 0 else text

    # Cerca cognome
    cognome_match = COGNOME_PATTERN.search(search_text)
    if cognome_match:
        cognome = cognome_match.group(1).strip()
        # Pulisci: prendi solo la prima riga (evita di catturare troppo)
        cognome = cognome.split("\n")[0].strip()
        # Rimuovi eventuali numeri o caratteri spuri alla fine
        cognome = re.sub(r"\d.*$", "", cognome).strip()

    # Cerca nome
    nome_match = NOME_PATTERN.search(search_text)
    if nome_match:
        nome = nome_match.group(1).strip()
        nome = nome.split("\n")[0].strip()
        nome = re.sub(r"\d.*$", "", nome).strip()

    # Fallback: se non trovati con pattern, prova approccio posizionale
    # Cerca il CF del percipiente e prendi le righe vicine
    if not cognome and not nome:
        cf = _extract_percipiente_cf(text)
        if cf:
            cf_pos = text.find(cf)
            if cf_pos > 0:
                # Cerca nelle righe precedenti al CF
                before_cf = text[:cf_pos]
                lines = [l.strip() for l in before_cf.split("\n") if l.strip()]
                # Le ultime righe prima del CF spesso contengono cognome e nome
                for line in reversed(lines[-5:]):
                    # Una riga con solo lettere e spazi potrebbe essere un nome
                    if re.match(r"^[A-ZÀ-Ú\s']+$", line) and len(line) > 2:
                        parts = line.split()
                        if len(parts) >= 2 and not cognome:
                            cognome = parts[0]
                            nome = " ".join(parts[1:])
                            break

    return cognome.upper().strip(), nome.upper().strip()


def parse_pdf(pdf_bytes: bytes) -> list[CURecord]:
    """
    Analizza un PDF massivo e restituisce la lista delle CU trovate.

    Args:
        pdf_bytes: contenuto del PDF come bytes

    Returns:
        Lista di CURecord con i dati di ogni CU trovata
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    boundaries = _find_cu_boundaries(doc)

    if not boundaries:
        doc.close()
        return []

    records = []
    for i, (start_page, anno) in enumerate(boundaries):
        # La CU finisce alla pagina prima dell'inizio della prossima,
        # o all'ultima pagina del documento
        if i + 1 < len(boundaries):
            end_page = boundaries[i + 1][0] - 1
        else:
            end_page = len(doc) - 1

        # Estrai testo completo della CU
        full_text = ""
        for page_idx in range(start_page, end_page + 1):
            full_text += doc[page_idx].get_text("text") + "\n"

        cf = _extract_percipiente_cf(full_text)
        cognome, nome = _extract_nome_cognome(full_text)

        record = CURecord(
            index=i + 1,
            start_page=start_page,
            end_page=end_page,
            anno=anno,
            codice_fiscale=cf,
            cognome=cognome,
            nome=nome,
            raw_text=full_text,
        )
        records.append(record)

    doc.close()
    return records


def export_single_cu(pdf_bytes: bytes, record: CURecord) -> bytes:
    """
    Esporta una singola CU come PDF.

    Args:
        pdf_bytes: contenuto del PDF originale
        record: CURecord con le pagine da estrarre

    Returns:
        bytes del PDF della singola CU
    """
    src_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    dst_doc = fitz.open()

    for page_idx in range(record.start_page, record.end_page + 1):
        dst_doc.insert_pdf(src_doc, from_page=page_idx, to_page=page_idx)

    output = dst_doc.tobytes()
    dst_doc.close()
    src_doc.close()
    return output


def export_all_as_zip(pdf_bytes: bytes, records: list[CURecord]) -> bytes:
    """
    Esporta tutte le CU come file ZIP.

    Args:
        pdf_bytes: contenuto del PDF originale
        records: lista di CURecord

    Returns:
        bytes del file ZIP contenente tutti i PDF singoli
    """
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for record in records:
            cu_pdf = export_single_cu(pdf_bytes, record)
            zf.writestr(record.filename, cu_pdf)

    return zip_buffer.getvalue()
