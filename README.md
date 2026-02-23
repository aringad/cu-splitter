# CU Splitter — Separa e Invia le Certificazioni Uniche in Automatico

**CU Splitter** è un'applicazione web gratuita e open source per **separare, rinominare e inviare via email le Certificazioni Uniche (CU/ex CUD)** estratte dal PDF massivo generato dai gestionali paghe come Zucchetti, TeamSystem, Paghe.net e altri.

Basta caricare il PDF, e il tool fa tutto: **split automatico**, **rinomina per dipendente**, **matching con anagrafica** e **invio email con allegato**.

## Il Problema

Ogni anno commercialisti, consulenti del lavoro e uffici paghe ricevono dal gestionale un **unico PDF enorme** con tutte le CU dei dipendenti. Devono:

1. Aprire il PDF e capire dove inizia e finisce ogni CU
2. Separare manualmente ogni certificazione (2-3 pagine ciascuna)
3. Rinominare ogni file con cognome, nome e codice fiscale
4. Inviare via email ogni CU al rispettivo dipendente

Con 50, 100 o 500 dipendenti questo processo richiede ore. **CU Splitter lo fa in pochi secondi.**

## Funzionalità

- **Split automatico PDF** — Riconosce l'inizio di ogni CU nel PDF e le separa in file singoli
- **Estrazione dati** — Legge automaticamente cognome, nome e codice fiscale del percipiente
- **Rinomina intelligente** — Ogni file viene nominato `CU2025_Cognome_Nome_CODICEFISCALE.pdf`
- **Download ZIP** — Scarica tutte le CU separate in un unico archivio ZIP
- **Matching con anagrafica** — Carica un CSV/Excel con i dati dei dipendenti e il sistema abbina automaticamente ogni CU alla email corrispondente
- **Match su codice fiscale** (esatto) e **fuzzy matching su cognome+nome** per gestire varianti e errori di battitura
- **Invio email massivo** — Invia ogni CU al dipendente con un click, con template email personalizzabile
- **Configurazione SMTP da interfaccia** — Nessun file da modificare: configura Gmail, Outlook, Aruba o qualsiasi server SMTP direttamente dall'app
- **Docker ready** — Un comando per avviare tutto: `docker-compose up`

## Requisiti

- **Docker** (consigliato) oppure **Python 3.11+**

## Installazione e Avvio

### Con Docker (consigliato)

```bash
git clone https://github.com/aringad/cu-splitter.git
cd cu-splitter
docker-compose up --build
```

Apri il browser su **http://localhost:8501** e sei operativo.

### Senza Docker

```bash
git clone https://github.com/aringad/cu-splitter.git
cd cu-splitter
pip install -r requirements.txt
streamlit run app.py
```

## Come si Usa

### 1. Upload e Split

- Carica il PDF massivo con tutte le Certificazioni Uniche
- Clicca **Analizza PDF**
- Visualizza la tabella con tutte le CU trovate (cognome, nome, codice fiscale, pagine)
- Clicca **Scarica ZIP** per ottenere tutti i PDF separati e rinominati

### 2. Matching con Anagrafica (opzionale)

- Carica un file CSV o Excel con le colonne: `cognome`, `nome`, `codice_fiscale`, `email`
- Il sistema abbina ogni CU al dipendente corrispondente
- I match vengono mostrati con colori:
  - **Verde**: match trovato automaticamente
  - **Arancione**: CU senza match (puoi inserire l'email manualmente)
  - **Grigio**: dipendenti in anagrafica senza CU corrispondente
- Correggi eventuali errori e conferma

### 3. Invio Email

- Configura il server SMTP dalla sidebar (host, porta, utente, password, TLS)
- Usa il bottone **Test connessione** per verificare
- Personalizza oggetto e corpo dell'email con i placeholder `{nome}`, `{cognome}`, `{anno}`
- Visualizza l'anteprima dell'email
- Clicca **Invia tutte** per l'invio massivo
- Controlla il log degli invii con stato di successo o errore per ogni destinatario

## Configurazione SMTP

La configurazione SMTP si fa direttamente dall'interfaccia web nella sidebar. Esempi comuni:

| Provider | Host | Porta | TLS |
|----------|------|-------|-----|
| Gmail | smtp.gmail.com | 587 | Sì |
| Outlook/Office 365 | smtp.office365.com | 587 | Sì |
| Aruba | smtps.aruba.it | 465 | No (SSL) |
| Libero | smtp.libero.it | 465 | No (SSL) |

> **Gmail**: è necessario generare una [password per le app](https://myaccount.google.com/apppasswords) (non usare la password dell'account).

In alternativa, puoi preimpostare i valori nel file `.env`:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tuo@email.com
SMTP_PASSWORD=app_password
SMTP_FROM=tuo@email.com
SMTP_TLS=true
```

## Formato Anagrafica

Il file CSV o Excel deve contenere almeno queste colonne (i nomi sono flessibili):

| cognome | nome | codice_fiscale | email |
|---------|------|----------------|-------|
| ROSSI | MARIO | RSSMRA80A01H501Z | mario.rossi@email.it |
| BIANCHI | LAURA | BNCLRA85B42F205X | laura.bianchi@email.it |

- Il separatore CSV viene rilevato automaticamente (virgola, punto e virgola, tab)
- I file Excel `.xlsx` e `.xls` sono supportati

## Stack Tecnologico

- **[Streamlit](https://streamlit.io/)** — interfaccia web
- **[PyMuPDF](https://pymupdf.readthedocs.io/)** — parsing e manipolazione PDF
- **[thefuzz](https://github.com/seatgeek/thefuzz)** — fuzzy matching sui nomi
- **[pandas](https://pandas.pydata.org/)** — lettura CSV/Excel
- **Docker** — distribuzione e deploy

## Gestionali Supportati

Il parser è progettato per funzionare con i PDF generati dai principali gestionali paghe italiani:

- **Zucchetti** (Paghe Web, HR Infinity)
- **TeamSystem** (Polyedro, Lynfa)
- **Paghe.net**
- **Buffetti**
- E qualsiasi gestionale che produca un PDF con testo selezionabile e l'intestazione "CERTIFICAZIONE UNICA"

## Licenza

MIT — libero per uso personale e commerciale.
