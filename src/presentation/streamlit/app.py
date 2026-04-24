"""Streamlit entrypoint — İzmir Hava Kalitesi Analiz.

TODO (Hafta 13): Full implementation by `analytics-engineer` agent.
Run: `streamlit run src/presentation/streamlit/app.py`
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="İzmir Hava Kalitesi",
    page_icon="🌫️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("İzmir Hava Kalitesi İzleme")
st.markdown(
    """
    **YZM536 Data Engineering Projesi** — Uçtan uca veri boru hattı.

    Sol menüden analiz sayfasını seçin:
    - **Tarihsel Analiz** — Geçmiş ölçümler, trendler
    - **Korelasyon** — İstasyonlar ve kirleticiler arası ilişki
    - **Karşılaştırma** — İstasyonlar arası karşılaştırma
    - **Tahmin** — 24 saatlik AQI tahmini (Hafta 14+)
    - **Rapor İndir** — CSV/PDF export
    """
)

st.info("Uygulama geliştirme aşamasında. Hafta 13'te tamamlanacak.")
