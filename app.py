"""
CU Splitter — Applicazione Streamlit per separare, rinominare e inviare
le Certificazioni Uniche (CU) estratte da un PDF massivo.
"""

import os
import time

import streamlit as st

from lib.pdf_parser import parse_pdf, export_single_cu, export_all_as_zip, CURecord
from lib.matcher import (
    load_anagrafica,
    match_cu_with_anagrafica,
    MatchStatus,
    MatchResult,
)
from lib.mailer import (
    SMTPConfig,
    load_default_template,
    render_template,
    test_smtp_connection,
    send_cu_email,
    SendStatus,
)

# --- Page config ---
st.set_page_config(
    page_title="CU Splitter",
    page_icon="📄",
    layout="wide",
)

# --- Session state init ---
if "cu_records" not in st.session_state:
    st.session_state.cu_records = []
if "pdf_bytes" not in st.session_state:
    st.session_state.pdf_bytes = None
if "match_results" not in st.session_state:
    st.session_state.match_results = []
if "matching_confirmed" not in st.session_state:
    st.session_state.matching_confirmed = False
if "send_logs" not in st.session_state:
    st.session_state.send_logs = []
if "email_subject" not in st.session_state:
    st.session_state.email_subject = "Certificazione Unica {anno}"
if "email_template" not in st.session_state:
    st.session_state.email_template = load_default_template()


# --- Sidebar: SMTP Configuration ---
def render_smtp_sidebar() -> SMTPConfig | None:
    """Renderizza la sidebar per la configurazione SMTP."""
    with st.sidebar:
        st.header("Configurazione SMTP")

        smtp_host = st.text_input(
            "Host SMTP",
            value=os.getenv("SMTP_HOST", "smtp.gmail.com"),
            key="smtp_host",
        )
        smtp_port = st.number_input(
            "Porta",
            value=int(os.getenv("SMTP_PORT", "587")),
            min_value=1,
            max_value=65535,
            key="smtp_port",
        )
        smtp_user = st.text_input(
            "Utente",
            value=os.getenv("SMTP_USER", ""),
            key="smtp_user",
        )
        smtp_password = st.text_input(
            "Password",
            value=os.getenv("SMTP_PASSWORD", ""),
            type="password",
            key="smtp_password",
        )
        smtp_from = st.text_input(
            "Indirizzo mittente",
            value=os.getenv("SMTP_FROM", ""),
            key="smtp_from",
        )
        smtp_tls = st.checkbox(
            "Usa TLS",
            value=os.getenv("SMTP_TLS", "true").lower() == "true",
            key="smtp_tls",
        )

        if st.button("Test connessione", key="test_smtp"):
            if not smtp_host or not smtp_user or not smtp_password:
                st.error("Compila tutti i campi SMTP.")
            else:
                config = SMTPConfig(
                    host=smtp_host,
                    port=int(smtp_port),
                    user=smtp_user,
                    password=smtp_password,
                    from_address=smtp_from or smtp_user,
                    use_tls=smtp_tls,
                )
                with st.spinner("Test in corso..."):
                    success, message = test_smtp_connection(config)
                if success:
                    st.success(message)
                else:
                    st.error(message)

        st.divider()
        st.caption("I dati SMTP restano in memoria per questa sessione.")

        # Ritorna config solo se compilata
        if smtp_host and smtp_user and smtp_password:
            return SMTPConfig(
                host=smtp_host,
                port=int(smtp_port),
                user=smtp_user,
                password=smtp_password,
                from_address=smtp_from or smtp_user,
                use_tls=smtp_tls,
            )
        return None


smtp_config = render_smtp_sidebar()

# --- Main content ---
st.title("CU Splitter")
st.caption("Separa, rinomina e invia le Certificazioni Uniche dal PDF del gestionale.")

tab1, tab2, tab3 = st.tabs(["1. Upload & Split", "2. Matching Anagrafica", "3. Invio Email"])


# ============================================================
# TAB 1: Upload e Split PDF
# ============================================================
with tab1:
    st.header("Upload e Split PDF")

    uploaded_pdf = st.file_uploader(
        "Carica il PDF con le Certificazioni Uniche",
        type=["pdf"],
        key="pdf_uploader",
    )

    if uploaded_pdf is not None:
        pdf_bytes = uploaded_pdf.read()
        st.session_state.pdf_bytes = pdf_bytes

        if st.button("Analizza PDF", type="primary", key="analyze_btn"):
            with st.spinner("Analisi del PDF in corso..."):
                records = parse_pdf(pdf_bytes)
                st.session_state.cu_records = records
                # Reset matching quando si carica un nuovo PDF
                st.session_state.match_results = []
                st.session_state.matching_confirmed = False
                st.session_state.send_logs = []

    # Mostra risultati
    records = st.session_state.cu_records
    if records:
        st.success(f"Trovate **{len(records)}** Certificazioni Uniche.")

        # Tabella riepilogativa
        table_data = []
        for r in records:
            table_data.append({
                "N.": r.index,
                "Cognome": r.cognome,
                "Nome": r.nome,
                "Codice Fiscale": r.codice_fiscale,
                "Pagine": f"{r.start_page + 1}-{r.end_page + 1}",
                "File": r.filename,
            })
        st.dataframe(table_data, use_container_width=True, hide_index=True)

        # Download ZIP
        if st.session_state.pdf_bytes:
            with st.spinner("Generazione ZIP..."):
                zip_bytes = export_all_as_zip(st.session_state.pdf_bytes, records)

            anno = records[0].anno if records else "CU"
            st.download_button(
                label=f"Scarica tutte le CU come ZIP ({len(records)} file)",
                data=zip_bytes,
                file_name=f"CU_{anno}_tutte.zip",
                mime="application/zip",
                type="primary",
            )

    elif uploaded_pdf is not None and st.session_state.pdf_bytes:
        st.info("Clicca **Analizza PDF** per iniziare l'estrazione.")


# ============================================================
# TAB 2: Matching con Anagrafica
# ============================================================
with tab2:
    st.header("Matching con Anagrafica")

    if not st.session_state.cu_records:
        st.warning("Prima carica e analizza un PDF nel tab **Upload & Split**.")
    else:
        uploaded_csv = st.file_uploader(
            "Carica anagrafica (CSV o Excel)",
            type=["csv", "xlsx", "xls"],
            key="csv_uploader",
            help="Colonne attese: cognome, nome, codice_fiscale (o cf), email",
        )

        if uploaded_csv is not None:
            if st.button("Esegui Matching", type="primary", key="match_btn"):
                with st.spinner("Matching in corso..."):
                    csv_bytes = uploaded_csv.read()
                    anagrafica = load_anagrafica(csv_bytes, uploaded_csv.name)
                    results = match_cu_with_anagrafica(
                        st.session_state.cu_records, anagrafica
                    )
                    st.session_state.match_results = results
                    st.session_state.matching_confirmed = False

        # Mostra risultati matching
        results = st.session_state.match_results
        if results:
            # Conta per stato
            matched = [r for r in results if r.status == MatchStatus.MATCHED]
            unmatched_cu = [r for r in results if r.status == MatchStatus.CU_UNMATCHED]
            unmatched_ana = [r for r in results if r.status == MatchStatus.ANAGRAFICA_UNMATCHED]

            col1, col2, col3 = st.columns(3)
            col1.metric("Match trovati", len(matched))
            col2.metric("CU senza match", len(unmatched_cu))
            col3.metric("Anagrafica senza CU", len(unmatched_ana))

            st.divider()

            # --- Sezione Match trovati (verde) ---
            if matched:
                st.subheader("CU con match trovato")
                for i, r in enumerate(matched):
                    with st.container():
                        cols = st.columns([3, 3, 2, 3, 2])
                        cols[0].write(f"**{r.cu.cognome} {r.cu.nome}**")
                        cols[1].write(f"CF: `{r.cu.codice_fiscale}`")
                        cols[2].write(f"Score: {r.match_score}% ({r.match_method})")
                        # Email editabile
                        new_email = cols[3].text_input(
                            "Email",
                            value=r.email,
                            key=f"email_matched_{i}",
                            label_visibility="collapsed",
                        )
                        r.email = new_email
                        cols[4].markdown(
                            '<span style="color: green;">&#x2714; Match</span>',
                            unsafe_allow_html=True,
                        )

            # --- Sezione CU senza match (giallo) ---
            if unmatched_cu:
                st.subheader("CU senza match — assegna email manualmente")
                for i, r in enumerate(unmatched_cu):
                    with st.container():
                        cols = st.columns([3, 3, 3, 2])
                        cols[0].write(f"**{r.cu.cognome} {r.cu.nome}**")
                        cols[1].write(f"CF: `{r.cu.codice_fiscale}`")
                        new_email = cols[2].text_input(
                            "Email",
                            value="",
                            key=f"email_unmatched_{i}",
                            label_visibility="collapsed",
                            placeholder="inserisci email...",
                        )
                        r.email = new_email
                        cols[3].markdown(
                            '<span style="color: orange;">&#x26A0; No match</span>',
                            unsafe_allow_html=True,
                        )

            # --- Sezione Anagrafica senza CU (grigio) ---
            if unmatched_ana:
                with st.expander(f"Anagrafiche senza CU ({len(unmatched_ana)})"):
                    for r in unmatched_ana:
                        a = r.anagrafica
                        if a:
                            st.write(
                                f"- {a.cognome} {a.nome} — CF: {a.codice_fiscale} — {a.email}"
                            )

            st.divider()
            if st.button("Conferma Matching", type="primary", key="confirm_match_btn"):
                st.session_state.matching_confirmed = True
                st.success("Matching confermato! Vai al tab **Invio Email**.")


# ============================================================
# TAB 3: Invio Email
# ============================================================
with tab3:
    st.header("Invio Email")

    if not st.session_state.matching_confirmed:
        st.warning(
            "Prima esegui e conferma il matching nel tab **Matching Anagrafica**."
        )
    elif smtp_config is None:
        st.warning("Configura le credenziali SMTP nella **sidebar** a sinistra.")
    else:
        # Filtra solo le CU con email (matched + unmatched con email manuale)
        sendable = [
            r for r in st.session_state.match_results
            if r.email
            and r.status != MatchStatus.ANAGRAFICA_UNMATCHED
            and r.cu.start_page >= 0
        ]

        if not sendable:
            st.info("Nessuna CU con indirizzo email assegnato.")
        else:
            st.write(f"**{len(sendable)}** email da inviare.")

            # Template personalizzabile
            st.subheader("Template Email")
            col_subj, _ = st.columns([2, 1])
            with col_subj:
                subject_template = st.text_input(
                    "Oggetto",
                    value=st.session_state.email_subject,
                    key="email_subject_input",
                    help="Usa {anno} per l'anno, {nome}, {cognome}",
                )
                st.session_state.email_subject = subject_template

            body_template = st.text_area(
                "Corpo email (HTML)",
                value=st.session_state.email_template,
                height=200,
                key="email_body_input",
                help="Placeholder disponibili: {nome}, {cognome}, {anno}",
            )
            st.session_state.email_template = body_template

            # Preview
            st.subheader("Anteprima")
            preview_record = sendable[0]
            preview_anno = preview_record.cu.anno or "2025"
            preview_subject = render_template(
                subject_template,
                preview_record.cu.nome,
                preview_record.cu.cognome,
                preview_anno,
            )
            preview_body = render_template(
                body_template,
                preview_record.cu.nome,
                preview_record.cu.cognome,
                preview_anno,
            )
            with st.expander("Anteprima email (primo destinatario)", expanded=True):
                st.write(f"**A:** {preview_record.email}")
                st.write(f"**Oggetto:** {preview_subject}")
                st.divider()
                st.html(preview_body)

            st.divider()

            # Tabella riepilogo destinatari
            st.subheader("Destinatari")
            dest_data = []
            for r in sendable:
                dest_data.append({
                    "Cognome": r.cu.cognome,
                    "Nome": r.cu.nome,
                    "CF": r.cu.codice_fiscale,
                    "Email": r.email,
                    "File": r.cu.filename,
                })
            st.dataframe(dest_data, use_container_width=True, hide_index=True)

            st.divider()

            # Invio
            col_send_all, col_send_single = st.columns(2)

            with col_send_all:
                if st.button(
                    f"Invia tutte ({len(sendable)} email)",
                    type="primary",
                    key="send_all_btn",
                ):
                    st.session_state.send_logs = []
                    progress_bar = st.progress(0)
                    status_text = st.empty()

                    for idx, r in enumerate(sendable):
                        anno = r.cu.anno or "2025"
                        subject = render_template(
                            subject_template, r.cu.nome, r.cu.cognome, anno
                        )
                        body = render_template(
                            body_template, r.cu.nome, r.cu.cognome, anno
                        )
                        pdf_data = export_single_cu(
                            st.session_state.pdf_bytes, r.cu
                        )

                        status_text.write(
                            f"Invio {idx + 1}/{len(sendable)}: {r.email}..."
                        )
                        log = send_cu_email(
                            config=smtp_config,
                            to_address=r.email,
                            subject=subject,
                            body_html=body,
                            pdf_bytes=pdf_data,
                            pdf_filename=r.cu.filename,
                        )
                        st.session_state.send_logs.append(log)
                        progress_bar.progress((idx + 1) / len(sendable))

                        # Piccola pausa tra invii per non sovraccaricare l'SMTP
                        if idx < len(sendable) - 1:
                            time.sleep(0.5)

                    status_text.empty()
                    successes = sum(
                        1
                        for l in st.session_state.send_logs
                        if l.status == SendStatus.SUCCESS
                    )
                    errors = sum(
                        1
                        for l in st.session_state.send_logs
                        if l.status == SendStatus.ERROR
                    )
                    if errors == 0:
                        st.success(f"Tutte le {successes} email inviate con successo!")
                    else:
                        st.warning(
                            f"Invio completato: {successes} riuscite, {errors} errori."
                        )

            # Log invii
            if st.session_state.send_logs:
                st.subheader("Log Invii")
                log_data = []
                for log in st.session_state.send_logs:
                    status_icon = (
                        "✅" if log.status == SendStatus.SUCCESS else "❌"
                    )
                    log_data.append({
                        "Stato": status_icon,
                        "Destinatario": log.to,
                        "Oggetto": log.subject,
                        "File": log.filename,
                        "Orario": log.timestamp,
                        "Errore": log.error or "-",
                    })
                st.dataframe(log_data, use_container_width=True, hide_index=True)
