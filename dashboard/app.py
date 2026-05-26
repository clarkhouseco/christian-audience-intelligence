"""
Christian Audience Intelligence Dashboard
Streamlit app — reads from Supabase dashboard views.

Local:  streamlit run dashboard/app.py
Cloud:  deploy via Streamlit Community Cloud, set SUPABASE_URL +
        SUPABASE_SERVICE_KEY in the app's Secrets settings.
"""

import os
import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv
from supabase import create_client, Client

# Allow imports from project root when running locally
sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Christian Audience Intelligence",
    page_icon="✝️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Supabase connection ───────────────────────────────────────────────────────
@st.cache_resource
def _client() -> Client:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_SERVICE_KEY"]
    except (KeyError, FileNotFoundError):
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        st.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY. "
                 "Check your .env file or Streamlit secrets.")
        st.stop()
    return create_client(url, key)


# ── Data loaders (cached 1 hour) ──────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_national() -> pd.DataFrame:
    rows = _client().table("v_national_scores").select("*").execute().data or []
    return pd.DataFrame(rows)

@st.cache_data(ttl=3600)
def load_state() -> pd.DataFrame:
    rows = _client().table("v_state_scores").select("*").execute().data or []
    return pd.DataFrame(rows)

@st.cache_data(ttl=3600)
def load_emerging() -> pd.DataFrame:
    rows = _client().table("v_emerging").select("*").execute().data or []
    return pd.DataFrame(rows)

@st.cache_data(ttl=3600)
def load_trend() -> pd.DataFrame:
    rows = _client().table("v_category_trend").select("*").execute().data or []
    df = pd.DataFrame(rows)
    if not df.empty and "scored_at" in df.columns:
        df["scored_at"] = pd.to_datetime(df["scored_at"])
    return df

@st.cache_data(ttl=3600)
def load_youtube() -> pd.DataFrame:
    rows = _client().table("v_youtube_top").select("*").execute().data or []
    return pd.DataFrame(rows)

@st.cache_data(ttl=3600)
def load_queries() -> pd.DataFrame:
    rows = _client().table("v_rising_queries").select("*").execute().data or []
    return pd.DataFrame(rows)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _label(cat: str) -> str:
    return cat.replace("_", " ").title()

def _no_data(msg: str):
    st.info(f"📭  {msg}", icon="ℹ️")


# ── Header ────────────────────────────────────────────────────────────────────
st.title("✝️  Christian Audience Intelligence")
st.caption(
    "Ministry strategy dashboard · Signals from Google Trends + YouTube · "
    "Updated weekly"
)
st.divider()

tab_overview, tab_geo, tab_trend, tab_yt, tab_rq = st.tabs([
    "🏠  Overview",
    "🗺️  Geographic",
    "📈  Trends Over Time",
    "🎬  YouTube",
    "🔍  Rising Queries",
])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 1 — OVERVIEW
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab_overview:
    df_nat = load_national()
    df_em  = load_emerging()

    if df_nat.empty:
        _no_data("No national scores yet. Run `python processors/scorer.py` to generate data.")
    else:
        # ── KPI cards ────────────────────────────────────────────────────────
        top      = df_nat.sort_values("composite_score", ascending=False).iloc[0]
        n_em     = len(df_em[df_em["region_type"] == "national"]) if not df_em.empty else 0
        last_run = str(df_nat["scored_at"].max())[:10] if "scored_at" in df_nat.columns else "—"

        c1, c2, c3 = st.columns(3)
        c1.metric(
            "🔥 Top National Topic",
            _label(top["category"]),
            delta=f"{top['wow_pct']:+.1f}% WoW" if pd.notna(top.get("wow_pct")) else None,
        )
        c2.metric("⚡ Emerging Nationally", n_em)
        c3.metric("📅 Last Scored", last_run)

        # ── Emerging alerts ───────────────────────────────────────────────────
        if not df_em.empty:
            nat_em = df_em[df_em["region_type"] == "national"].copy()
            if not nat_em.empty:
                st.subheader("⚡ Nationally Emerging Right Now")
                for _, row in nat_em.sort_values("composite_score", ascending=False).iterrows():
                    with st.container(border=True):
                        col_a, col_b = st.columns([4, 1])
                        col_a.markdown(
                            f"**{_label(row['category'])}**  \n"
                            f"<sub>Top terms: {row.get('top_terms', '—')}</sub>",
                            unsafe_allow_html=True,
                        )
                        col_b.metric(
                            "Score",
                            f"{row['composite_score']:.0f} / 100",
                            delta=f"{row['wow_pct']:+.0f}%" if pd.notna(row.get("wow_pct")) else None,
                        )
                st.divider()

        # ── Category bar chart ────────────────────────────────────────────────
        st.subheader("📊 All National Topic Scores")
        df_sorted = df_nat.sort_values("composite_score", ascending=True).copy()
        df_sorted["Category"] = df_sorted["category"].apply(_label)

        fig = px.bar(
            df_sorted,
            x="composite_score",
            y="Category",
            orientation="h",
            color="composite_score",
            color_continuous_scale="Blues",
            text="composite_score",
            labels={"composite_score": "Score (0–100)"},
            height=max(380, len(df_sorted) * 40),
        )
        fig.update_traces(
            texttemplate="%{text:.1f}",
            textposition="outside",
        )
        fig.update_layout(
            coloraxis_showscale=False,
            margin=dict(l=10, r=50, t=10, b=10),
            yaxis_title="",
        )
        st.plotly_chart(fig, use_container_width=True)

        # ── Raw table ─────────────────────────────────────────────────────────
        with st.expander("Full data table"):
            cols = ["category_label", "composite_score", "google_score",
                    "youtube_score", "wow_pct", "is_emerging", "top_terms"]
            cols = [c for c in cols if c in df_nat.columns]
            st.dataframe(
                df_nat[cols].sort_values("composite_score", ascending=False),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "category_label": st.column_config.TextColumn("Category"),
                    "composite_score": st.column_config.NumberColumn(
                        "Composite Score",
                        help=(
                            "Combined momentum score from 0–100.  \n\n"
                            "Blends Google Trends search momentum (65%) and YouTube view "
                            "velocity (35%).  \n"
                            "• 50 = neutral / flat interest  \n"
                            "• Above 60 = growing interest  \n"
                            "• Below 40 = declining interest"
                        ),
                        format="%.1f",
                    ),
                    "google_score": st.column_config.NumberColumn(
                        "Google Score",
                        help=(
                            "Google Trends momentum score from 0–100.  \n\n"
                            "Measures week-over-week change in search interest for all terms "
                            "in this category.  \n"
                            "• 50 = no change  \n"
                            "• 75 = search interest up ~50% week-over-week  \n"
                            "• 25 = search interest down ~50% week-over-week"
                        ),
                        format="%.1f",
                    ),
                    "youtube_score": st.column_config.NumberColumn(
                        "YouTube Score",
                        help=(
                            "YouTube engagement velocity score from 0–100.  \n\n"
                            "Measures how fast videos in this category are accumulating views "
                            "relative to all other categories. Log-normalized so one viral video "
                            "doesn't collapse every other score to near-zero."
                        ),
                        format="%.1f",
                    ),
                    "wow_pct": st.column_config.NumberColumn(
                        "WoW %",
                        help=(
                            "Week-over-week percentage change in search interest.  \n\n"
                            "Positive = interest is growing. Negative = interest is declining.  \n"
                            "Calculated by comparing the average search index of the last 7 days "
                            "against the 7 days before that."
                        ),
                        format="%.1f%%",
                    ),
                    "is_emerging": st.column_config.CheckboxColumn(
                        "Emerging?",
                        help=(
                            "Flagged as emerging when all three conditions are true:  \n"
                            "1. Composite score > 60  \n"
                            "2. Week-over-week growth > 15%  \n"
                            "3. At least 2 search terms contributed data"
                        ),
                    ),
                    "top_terms": st.column_config.TextColumn(
                        "Top Terms",
                        help=(
                            "The specific search terms within this category that showed the "
                            "strongest upward momentum this week. Up to 3 terms listed."
                        ),
                    ),
                },
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 2 — GEOGRAPHIC
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab_geo:
    df_state = load_state()

    if df_state.empty:
        _no_data(
            "No state-level scores yet. Geographic data builds after the first "
            "full weekly collection run."
        )
    else:
        cats = sorted(df_state["category"].unique())
        sel_cat = st.selectbox("Topic category", cats, format_func=_label)

        df_c = df_state[df_state["category"] == sel_cat].copy()

        if df_c.empty:
            _no_data(f"No state data for {_label(sel_cat)} yet.")
        else:
            # Plotly USA-states uses 2-letter codes (no "US-" prefix)
            df_c["abbr"] = df_c["state_code"].str.replace("US-", "", regex=False)

            map_col, rank_col = st.columns([3, 1])

            with map_col:
                st.subheader(f"🗺️  {_label(sel_cat)} — Interest by State")
                fig_map = px.choropleth(
                    df_c,
                    locations="abbr",
                    locationmode="USA-states",
                    color="composite_score",
                    scope="usa",
                    color_continuous_scale="Blues",
                    hover_name="state",
                    hover_data={
                        "composite_score": ":.1f",
                        "wow_pct": ":.1f",
                        "top_terms": True,
                        "abbr": False,
                    },
                    labels={
                        "composite_score": "Score",
                        "wow_pct": "WoW %",
                        "top_terms": "Top Terms",
                    },
                    height=460,
                )
                fig_map.update_layout(
                    margin=dict(l=0, r=0, t=0, b=0),
                    coloraxis_colorbar=dict(title="Score"),
                )
                st.plotly_chart(fig_map, use_container_width=True)

            with rank_col:
                st.subheader("Top 10 States")
                top10 = df_c.sort_values("composite_score", ascending=False).head(10)
                for _, row in top10.iterrows():
                    st.metric(
                        row["state"],
                        f"{row['composite_score']:.1f}",
                        delta=f"{row['wow_pct']:+.1f}%" if pd.notna(row.get("wow_pct")) else None,
                    )

            with st.expander("All states — full table"):
                show = ["state", "composite_score", "google_score", "wow_pct", "top_terms"]
                show = [c for c in show if c in df_c.columns]
                st.dataframe(
                    df_c[show].sort_values("composite_score", ascending=False),
                    use_container_width=True,
                    hide_index=True,
                )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 3 — TRENDS OVER TIME
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab_trend:
    df_tr = load_trend()

    if df_tr.empty or ("scored_at" in df_tr.columns and df_tr["scored_at"].nunique() < 2):
        st.info(
            "📈  **Trend lines need at least 2 scored weeks to display.**\n\n"
            "The scorer runs automatically every Sunday. Come back after the next "
            "weekly run to see momentum lines here.",
            icon="⏳",
        )
        if not df_tr.empty:
            st.caption(
                f"Currently have {df_tr['scored_at'].nunique()} scored date(s). "
                f"Categories scored: {', '.join(_label(c) for c in sorted(df_tr['category'].unique()))}"
            )
    else:
        all_cats = sorted(df_tr["category"].unique())
        sel_cats = st.multiselect(
            "Categories to compare",
            all_cats,
            default=all_cats[:6],
            format_func=_label,
        )

        if not sel_cats:
            st.caption("Select at least one category above.")
        else:
            df_f = df_tr[df_tr["category"].isin(sel_cats)].copy()
            df_f["Category"] = df_f["category"].apply(_label)

            fig_line = px.line(
                df_f,
                x="scored_at",
                y="composite_score",
                color="Category",
                markers=True,
                labels={
                    "scored_at": "",
                    "composite_score": "Score (0–100)",
                },
                height=460,
            )
            fig_line.update_layout(
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                hovermode="x unified",
            )
            st.plotly_chart(fig_line, use_container_width=True)

            # Latest WoW table
            st.subheader("Latest Week-over-Week Change")
            latest = (
                df_f.sort_values("scored_at")
                .groupby("Category")
                .last()
                .reset_index()
            )
            wow_cols = ["Category", "composite_score", "wow_pct", "is_emerging"]
            wow_cols = [c for c in wow_cols if c in latest.columns]
            st.dataframe(
                latest[wow_cols].sort_values("wow_pct", ascending=False),
                use_container_width=True,
                hide_index=True,
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 4 — YOUTUBE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab_yt:
    df_yt = load_youtube()

    if df_yt.empty:
        _no_data("No YouTube data yet. Run `python collectors/youtube.py --mode search`.")
    else:
        yt_cats = ["All"] + sorted(df_yt["category"].dropna().unique())
        sel_yt = st.selectbox(
            "Filter by category",
            yt_cats,
            format_func=lambda x: "All Categories" if x == "All" else _label(x),
        )
        df_yf = df_yt if sel_yt == "All" else df_yt[df_yt["category"] == sel_yt]

        # ── Engagement % column ───────────────────────────────────────────────
        # Engagement = (likes + comments) / views × 100
        # Industry average is 1–4%. Above 4% = strong audience resonance.
        df_yf = df_yf.copy()
        likes    = pd.to_numeric(df_yf.get("like_count",    0), errors="coerce").fillna(0)
        comments = pd.to_numeric(df_yf.get("comment_count", 0), errors="coerce").fillna(0)
        views    = pd.to_numeric(df_yf.get("view_count",    1), errors="coerce").clip(lower=1)
        df_yf["engagement_pct"] = ((likes + comments) / views * 100).round(2)

        # ── Video type label ─────────────────────────────────────────────────
        def _video_type(row):
            if pd.isna(row.get("is_short")):
                return "—"          # duration not yet fetched
            return "🩳 Short" if row["is_short"] else "📹 Video"
        df_yf["type"] = df_yf.apply(_video_type, axis=1)

        # ── URLs ─────────────────────────────────────────────────────────────
        def _video_url(row):
            vid = row.get("video_id", "")
            if not vid:
                return ""
            return (f"https://www.youtube.com/shorts/{vid}"
                    if row.get("is_short") else
                    f"https://www.youtube.com/watch?v={vid}")

        df_yf["video_url"]   = df_yf.apply(_video_url, axis=1)
        df_yf["channel_url"] = df_yf["channel_id"].apply(
            lambda cid: f"https://www.youtube.com/channel/{cid}" if cid else ""
        )
        df_yf["thumbnail"]   = df_yf["video_id"].apply(
            lambda vid: f"https://img.youtube.com/vi/{vid}/mqdefault.jpg" if vid else ""
        )

        # ── KPIs ─────────────────────────────────────────────────────────────
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Videos Tracked", f"{len(df_yf):,}")
        k2.metric("Total Views",    f"{df_yf['view_count'].sum():,.0f}")
        k3.metric("Avg Views",      f"{df_yf['view_count'].mean():,.0f}")
        k4.metric("Avg Engagement", f"{df_yf['engagement_pct'].mean():.2f}%")

        st.divider()

        # ── Category bar chart (All view only) ───────────────────────────────
        if sel_yt == "All":
            st.subheader("📊 Average Views by Category")
            cat_avg = (
                df_yt.groupby("category")["view_count"]
                .mean()
                .reset_index()
                .sort_values("view_count", ascending=True)
            )
            cat_avg["Category"] = cat_avg["category"].apply(_label)
            fig_yt_bar = px.bar(
                cat_avg,
                x="view_count",
                y="Category",
                orientation="h",
                color="view_count",
                color_continuous_scale="Greens",
                text="view_count",
                labels={"view_count": "Avg Views"},
                height=max(320, len(cat_avg) * 38),
            )
            fig_yt_bar.update_traces(
                texttemplate="%{text:,.0f}",
                textposition="outside",
            )
            fig_yt_bar.update_layout(
                coloraxis_showscale=False,
                yaxis_title="",
                margin=dict(l=10, r=80, t=10, b=10),
            )
            st.plotly_chart(fig_yt_bar, use_container_width=True)

        # ── Video table ───────────────────────────────────────────────────────
        st.subheader(
            "🎬 Top Videos"
            + ("" if sel_yt == "All" else f" — {_label(sel_yt)}")
        )

        tbl = df_yf[[
            "thumbnail", "title", "video_url",
            "channel_name", "channel_url",
            "type", "category_label",
            "view_count", "like_count", "comment_count", "engagement_pct",
            "published_date",
        ]].sort_values("view_count", ascending=False).head(100)

        st.dataframe(
            tbl,
            use_container_width=True,
            hide_index=True,
            column_config={
                "thumbnail": st.column_config.ImageColumn(
                    "📸",
                    help="Video thumbnail. Click the ▶ Watch link in the next column to open it.",
                    width="small",
                ),
                "title": st.column_config.TextColumn(
                    "Title",
                    help="Video title as published on YouTube.",
                    width="large",
                ),
                "video_url": st.column_config.LinkColumn(
                    "▶ Watch",
                    help="Opens the video on YouTube. Shorts open at youtube.com/shorts/…",
                    display_text="▶ Watch",
                    width="small",
                ),
                "channel_name": st.column_config.TextColumn(
                    "Channel",
                    help="YouTube channel that published this video.",
                ),
                "channel_url": st.column_config.LinkColumn(
                    "↗ Channel",
                    help="Opens the creator's YouTube channel page.",
                    display_text="↗ Channel",
                    width="small",
                ),
                "type": st.column_config.TextColumn(
                    "Type",
                    help=(
                        "📹 Video = standard YouTube video (over 60 seconds).  \n"
                        "🩳 Short = YouTube Short (60 seconds or less, vertical format).  \n"
                        "— = duration not yet fetched (run stats collector to update)."
                    ),
                    width="small",
                ),
                "category_label": st.column_config.TextColumn(
                    "Category",
                    help="The Christian topic category this video was discovered under.",
                ),
                "view_count": st.column_config.NumberColumn(
                    "Views",
                    help="Total view count as of the last daily stats collection.",
                    format="%d",
                ),
                "like_count": st.column_config.NumberColumn(
                    "Likes",
                    help="Total likes as of the last daily stats collection. "
                         "Some creators disable like counts — those show as blank.",
                    format="%d",
                ),
                "comment_count": st.column_config.NumberColumn(
                    "Comments",
                    help="Total comment count as of the last daily stats collection.",
                    format="%d",
                ),
                "engagement_pct": st.column_config.NumberColumn(
                    "Engagement %",
                    help=(
                        "Engagement rate = (Likes + Comments) ÷ Views × 100.  \n\n"
                        "Measures how actively viewers interact with the content, not just watch it.  \n"
                        "Industry benchmarks: 1–2% = average · 2–4% = good · 4%+ = excellent.  \n"
                        "High engagement signals topics that resonate deeply with the audience."
                    ),
                    format="%.2f%%",
                ),
                "published_date": st.column_config.DateColumn(
                    "Published",
                    help="Date the video was published on YouTube.",
                ),
            },
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 5 — RISING QUERIES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab_rq:
    df_rq = load_queries()

    st.subheader("🔍 Rising Search Queries")
    st.caption(
        "Search terms Google detected as rapidly rising alongside your tracked terms. "
        "These signal emerging conversations worth watching."
    )

    if df_rq.empty:
        _no_data("No rising queries yet. They appear after the Google Trends collector runs.")
    else:
        rq_cats = ["All"] + sorted(df_rq["category"].dropna().unique())
        sel_rq = st.selectbox(
            "Filter by category",
            rq_cats,
            key="rq_filter",
            format_func=lambda x: "All Categories" if x == "All" else _label(x),
        )
        df_rf = df_rq if sel_rq == "All" else df_rq[df_rq["category"] == sel_rq]

        show_rq = df_rf[
            ["query", "source_term", "category_label", "rise_value", "week_of"]
        ].copy()

        st.dataframe(
            show_rq.sort_values("week_of", ascending=False),
            use_container_width=True,
            hide_index=True,
            column_config={
                "query": st.column_config.TextColumn(
                    "Rising Query",
                    help=(
                        "A search term that Google detected as rapidly growing in popularity, "
                        "found in association with one of your tracked search terms. "
                        "These are real searches people are making right now."
                    ),
                    width="medium",
                ),
                "source_term": st.column_config.TextColumn(
                    "Source Term",
                    help=(
                        "The term from your tracked list that this rising query was found "
                        "alongside. When people searched for the Source Term, Google also "
                        "detected this Rising Query growing nearby."
                    ),
                ),
                "category_label": st.column_config.TextColumn(
                    "Category",
                    help="The topic category the Source Term belongs to.",
                ),
                "rise_value": st.column_config.TextColumn(
                    "Rise Value",
                    help=(
                        "How fast this query is growing compared to the prior period.  \n\n"
                        "• A percentage (e.g. +320%) means search volume grew by that amount.  \n"
                        "• **Breakout** means growth exceeded 5,000% — essentially a brand-new "
                        "or suddenly viral search term with no meaningful prior baseline."
                    ),
                    width="small",
                ),
                "week_of": st.column_config.DateColumn(
                    "Week",
                    help="The week this rising query was detected during data collection.",
                    width="small",
                ),
            },
        )
