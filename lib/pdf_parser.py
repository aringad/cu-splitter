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


# Pattern per identificare l'inizio di una nuova CU.
# Gestisce vari formati di estrazione testo:
#   - "CERTIFICAZIONE UNICA 2025"        (standard con spazi)
#   - "CERTIFICAZIONE\nUNICA2025"         (a capo, anno attaccato)
#   - "CERTIFICAZIONE UNICA\n2025"        (anno su riga separata)
#   - "CERTIFICAZIONE\nUNICA\n2025"       (tutto separato)
#   - "C E R T I F I C A Z I O N E ..."   (lettere spaziate)
CU_START_PATTERNS = [
    re.compile(r"CERTIFICAZIONE\s+UNICA\s*(\d{4})", re.IGNORECASE),
    re.compile(r"C\s*E\s*R\s*T\s*I\s*F\s*I\s*C\s*A\s*Z\s*I\s*O\s*N\s*E\s+U\s*N\s*I\s*C\s*A\s*(\d{4})", re.IGNORECASE),
]

# Codice fiscale italiano: 6 lettere + 2 cifre + 1 lettera + 2 cifre + 1 lettera + 3 cifre + 1 lettera
CF_PATTERN = re.compile(r"\b([A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z])\b")

# Pattern per identificare la sezione dati del percipiente
PERCIPIENTE_SECTION_PATTERNS = [
    re.compile(r"DATI\s+RELATIVI\s+AL\s+DIPENDENTE", re.IGNORECASE),
    re.compile(r"DATI\s+ANAGRAFICI\s+DEL\s+PERCIPIENTE", re.IGNORECASE),
    re.compile(r"DATI\s+RELATIVI\s+AL\s+PERCIPIENTE", re.IGNORECASE),
    re.compile(r"DATI\s+RELATIVI\s+AL\s+DIPENDENTE,?\s*\n?\s*PENSIONATO", re.IGNORECASE),
    re.compile(r"DATI\s+ANAGRAFICI", re.IGNORECASE),
]

# Pattern per cognome e nome nella sezione percipiente
COGNOME_PATTERN = re.compile(r"Cognome\s+o\s+Denominazione\s*[:\s]*([A-Z\s'À-Ú\-]+)", re.IGNORECASE)
NOME_PATTERN = re.compile(r"(?<![Cc]ognome\s)Nome\s*[:\s]*([A-Z\s'À-Ú\-]+)", re.IGNORECASE)


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

    Usa multiple strategie:
      1. Cerca il pattern "CERTIFICAZIONE UNICA <anno>" nel testo
      2. Cerca anche la dicitura con lettere spaziate
      3. Non filtra per posizione nel testo perché PyMuPDF può estrarre
         le label dei campi del form prima dell'intestazione
      4. Evita duplicati sulla stessa pagina
    """
    boundaries = []
    seen_pages: set[int] = set()

    for page_idx in range(len(doc)):
        if page_idx in seen_pages:
            continue

        page = doc[page_idx]
        text = page.get_text("text")

        # Prova tutti i pattern
        found = False
        for pattern in CU_START_PATTERNS:
            match = pattern.search(text)
            if match:
                anno = match.group(1)
                boundaries.append((page_idx, anno))
                seen_pages.add(page_idx)
                found = True
                break

        # Fallback: cerca "CERTIFICAZIONE" su una riga e "UNICA" + anno
        # nelle righe successive (testo molto frammentato)
        if not found:
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if re.search(r"CERTIFICAZIONE", line, re.IGNORECASE):
                    # Cerca "UNICA" e un anno nelle righe successive (max 3)
                    remaining = " ".join(lines[i:i+4])
                    m = re.search(r"CERTIFICAZIONE\s+UNICA\s*(\d{4})", remaining, re.IGNORECASE)
                    if m and page_idx not in seen_pages:
                        boundaries.append((page_idx, m.group(1)))
                        seen_pages.add(page_idx)
                        break

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
    Usa multiple strategie per gestire diversi formati PDF.
    """
    cognome = ""
    nome = ""

    # --- Strategia 1: Cerca cognome/nome nella sezione percipiente ---
    section_start = 0
    for pattern in PERCIPIENTE_SECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            section_start = match.end()
            break

    search_text = text[section_start:] if section_start > 0 else text

    cognome_match = COGNOME_PATTERN.search(search_text)
    if cognome_match:
        cognome = cognome_match.group(1).strip()
        cognome = cognome.split("\n")[0].strip()
        cognome = re.sub(r"\d.*$", "", cognome).strip()

    nome_match = NOME_PATTERN.search(search_text)
    if nome_match:
        nome = nome_match.group(1).strip()
        nome = nome.split("\n")[0].strip()
        nome = re.sub(r"\d.*$", "", nome).strip()

    # --- Strategia 2: approccio posizionale rispetto al CF percipiente ---
    if not cognome and not nome:
        cf = _extract_percipiente_cf(text)
        if cf:
            # Trova la SECONDA occorrenza del CF (la prima è nella sezione header,
            # la seconda è vicina ai dati anagrafici reali)
            cf_positions = [m.start() for m in re.finditer(re.escape(cf), text)]

            for cf_pos in cf_positions:
                # Cerca le righe DOPO il CF: spesso cognome e nome seguono
                after_cf = text[cf_pos + len(cf):]
                after_lines = [l.strip() for l in after_cf.split("\n") if l.strip()]

                for line in after_lines[:5]:
                    # Riga con solo lettere maiuscole/spazi/apostrofi -> probabile cognome o nome
                    if re.match(r"^[A-ZÀ-Ú\s\'\-]+$", line) and len(line) > 2:
                        if not cognome:
                            cognome = line.strip()
                        elif not nome:
                            nome = line.strip()
                            break

                if cognome:
                    break

                # Cerca anche prima del CF
                before_cf = text[:cf_pos]
                before_lines = [l.strip() for l in before_cf.split("\n") if l.strip()]
                for line in reversed(before_lines[-5:]):
                    if re.match(r"^[A-ZÀ-Ú\s\'\-]+$", line) and len(line) > 2:
                        parts = line.split()
                        if len(parts) >= 2 and not cognome:
                            cognome = parts[0]
                            nome = " ".join(parts[1:])
                            break

                if cognome:
                    break

    # Pulizia finale
    cognome = re.sub(r"\s{2,}", " ", cognome).strip()
    nome = re.sub(r"\s{2,}", " ", nome).strip()

    # Rimuovi valori che sono chiaramente label e non nomi
    label_words = {"COGNOME", "NOME", "DENOMINAZIONE", "CODICE", "FISCALE",
                   "SESSO", "DATA", "COMUNE", "PROVINCIA", "FIRMA",
                   "O DENOMINAZIONE", "CERTIFICAZIONE", "UNICA"}
    if cognome.upper().strip() in label_words:
        cognome = ""
    if nome.upper().strip() in label_words:
        nome = ""

    return cognome.upper().strip(), nome.upper().strip()


def parse_pdf(pdf_bytes: bytes) -> list[CURecord]:
    """
    Analizza un PDF massivo e restituisce la lista delle CU trovate.

    Ogni CU può essere composta da più sezioni nel PDF (es. frontespizio +
    dati fiscali), ognuna con l'intestazione "CERTIFICAZIONE UNICA".
    Le sezioni consecutive con lo stesso codice fiscale del percipiente
    vengono unite in un'unica CU.

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

    # Fase 1: costruisci i segmenti grezzi
    raw_segments = []
    for i, (start_page, anno) in enumerate(boundaries):
        if i + 1 < len(boundaries):
            end_page = boundaries[i + 1][0] - 1
        else:
            end_page = len(doc) - 1

        full_text = ""
        for page_idx in range(start_page, end_page + 1):
            full_text += doc[page_idx].get_text("text") + "\n"

        cf = _extract_percipiente_cf(full_text)

        raw_segments.append({
            "start_page": start_page,
            "end_page": end_page,
            "anno": anno,
            "cf": cf,
            "text": full_text,
        })

    # Fase 2: mergia segmenti consecutivi con lo stesso CF percipiente
    # (es. frontespizio + dati lavoro dipendente = stessa CU)
    merged = []
    for seg in raw_segments:
        if (merged
                and seg["cf"]
                and merged[-1]["cf"] == seg["cf"]):
            # Stesso percipiente → estendi il segmento precedente
            merged[-1]["end_page"] = seg["end_page"]
            merged[-1]["text"] += seg["text"]
        else:
            merged.append(dict(seg))

    # Fase 3: estrai nome/cognome dal testo combinato
    records = []
    for i, m in enumerate(merged):
        cognome, nome = _extract_nome_cognome(m["text"])

        records.append(CURecord(
            index=i + 1,
            start_page=m["start_page"],
            end_page=m["end_page"],
            anno=m["anno"],
            codice_fiscale=m["cf"],
            cognome=cognome,
            nome=nome,
            raw_text=m["text"],
        ))

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
