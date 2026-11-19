"""Order Explorer: a small Streamlit-in-Snowflake app to film with lab-video's live capture.

It reads only SNOWFLAKE_SAMPLE_DATA.TPCH_SF1 (the TPC-H sample share), so any account that has the sample data
can deploy it, and a video of it shows nobody's real numbers. Deploy notes: ../README.md (sample_app section).
"""
import streamlit as st

st.set_page_config(page_title="Order Explorer", page_icon=":material/bar_chart:", layout="wide")

TPCH = "SNOWFLAKE_SAMPLE_DATA.TPCH_SF1"
JOINS = f"""
FROM {TPCH}.ORDERS o
JOIN {TPCH}.CUSTOMER c ON c.C_CUSTKEY = o.O_CUSTKEY
JOIN {TPCH}.NATION n ON n.N_NATIONKEY = c.C_NATIONKEY
JOIN {TPCH}.REGION r ON r.R_REGIONKEY = n.N_REGIONKEY
"""

conn = st.connection("snowflake")


@st.cache_data(ttl=3600, show_spinner=False)
def regions():
    return conn.query(f"SELECT R_NAME FROM {TPCH}.REGION ORDER BY R_NAME")["R_NAME"].tolist()


@st.cache_data(ttl=3600, show_spinner="Reading orders...")
def summary(region):
    return conn.query(f"SELECT COUNT(*) AS ORDERS, SUM(o.O_TOTALPRICE) AS REVENUE, AVG(o.O_TOTALPRICE) AS AVG_ORDER"
                      f" {JOINS} WHERE r.R_NAME = ?", params=[region]).iloc[0]


@st.cache_data(ttl=3600, show_spinner=False)
def by_year(region):
    # FLOAT, not NUMBER: a decimal column reaches the chart as unscaled integers drawn as categories
    return conn.query(f"SELECT YEAR(o.O_ORDERDATE)::VARCHAR AS YEAR, (SUM(o.O_TOTALPRICE) / 1e9)::FLOAT AS REVENUE_BN"
                      f" {JOINS} WHERE r.R_NAME = ? GROUP BY 1 ORDER BY 1", params=[region])


st.title("Order Explorer")
st.caption("TPC-H sample data · SNOWFLAKE_SAMPLE_DATA.TPCH_SF1")

options = regions()
region = st.segmented_control("Region", options, default=options[0], selection_mode="single") or options[0]

s = summary(region)
c1, c2, c3 = st.columns(3)
c1.metric("Orders", f"{int(s['ORDERS']):,}")
c2.metric("Revenue", f"${float(s['REVENUE']) / 1e9:,.1f}B")
c3.metric("Average order", f"${float(s['AVG_ORDER']):,.0f}")

st.subheader(f"Revenue by year · {region.title()}")
st.bar_chart(by_year(region), x="YEAR", y="REVENUE_BN", y_label="Revenue ($B)", x_label="Order year", height=320)
