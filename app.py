import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import json
#import paho.mqtt.client as mqtt
from datetime import datetime

# --- Page Config & CSS ---
st.set_page_config(page_title="Smart Waste Dashboard", layout="wide", page_icon="♻️")

st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 🔌 STATE INITIALIZATION (Required for MQTT)
# ==========================================
# Starts at 0, waits for MQTT to send real data
if 'bin_data' not in st.session_state:
    st.session_state.bin_data = {"bin_a": 0, "bin_b": 0, "bin_c": 0}
if 'history' not in st.session_state:
    st.session_state.history = pd.DataFrame(columns=["Timestamp", "Bin", "Level"])

# ==========================================
# MQTT INTEGRATION
# ==========================================
MQTT_BROKER = "broker.hivemq.com" # TODO: Change to your broker IP
MQTT_TOPIC = "smartbin/levels"    # TODO: Change to your topic

# TODO: MQTT codes

# ==========================================
# MAIN UI LAYOUT (Single Page)
# ==========================================
st.title("♻️ Smart Waste System")
st.caption("Real-time Waste Management & Analytics Dashboard")
st.markdown("---")

# --- SECTION 1: LIVE CAPACITY ---
st.subheader("Live Bin Capacity")
cols = st.columns(3)

bins = [
    {"label": "General Waste", "key": "bin_a", "color": "#3498DB", "icon": "🗑️"},
    {"label": "Plastic", "key": "bin_b", "color": "#2ECC71", "icon": "♻️"},
    {"label": "Metal", "key": "bin_c", "color": "#E74C3C", "icon": "🔩"}
]

for i, bin_info in enumerate(bins):
    val = st.session_state.bin_data.get(bin_info['key'], 0)
    
    if val < 70:
        status, status_color, text_color = "NORMAL", "#D5F5E3", "#1D8348"
    elif val < 90:
        status, status_color, text_color = "NEARLY FULL", "#FCF3CF", "#9A7D0A"
    else:
        status, status_color, text_color = "CRITICAL", "#FDEDEC", "#943126"

    with cols[i]:
            st.markdown(f"""
    <div style="background-color: white; padding: 25px; border-radius: 20px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); border: 1px solid #f0f2f6; margin-bottom: 20px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
            <span style="font-size: 24px;">{bin_info['icon']}</span>
            <span style="background-color: {status_color}; color: {text_color}; padding: 4px 12px; border-radius: 20px; font-size: 10px; font-weight: bold;">{status}</span>
        </div>
        <h3 style="margin: 0; color: #5D6D7E; font-size: 16px; font-weight: 500;">{bin_info['label']}</h3>
        <h1 style="margin: 5px 0 20px 0; color: #2C3E50; font-size: 36px;">{val}<span style="font-size: 18px; color: #BDC3C7;">%</span></h1>
        <div style="background-color: #F2F4F4; height: 12px; width: 100%; border-radius: 10px; overflow: hidden;">
            <div style="background: linear-gradient(90deg, {bin_info['color']} 0%, #FFFFFF 200%); height: 100%; width: {val}%; border-radius: 10px; transition: width 0.8s cubic-bezier(0.4, 0, 0.2, 1);"></div>
        </div>
    </div>
        """, unsafe_allow_html=True)

st.markdown("---")

# TODO: replace this
# --- SECTION 2: ML ANALYSIS ---
st.subheader("Predictive Analytics & Classification")
c1, c2 = st.columns([2, 1])

with c1:
    st.write("**Fill Level Forecast (Next 6 Hours)**")
    # This chart remains static placeholder for now until you hook up a real ML model
    future_times = ["14:00", "15:00", "16:00", "17:00", "18:00", "19:00"]
    future_vals = [st.session_state.bin_data.get('bin_a', 0) + (i*5) for i in range(6)] 
    fig_ml = go.Figure()
    fig_ml.add_trace(go.Scatter(x=future_times, y=future_vals, mode='lines+markers', name='Predicted', line=dict(color='#4CAF50', dash='dot')))
    fig_ml.update_layout(height=250, margin=dict(l=0, r=0, t=30, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig_ml, width="stretch")

with c2:
    st.write("**Waste Classification (CNN)**")
    st.success("YOLOv8 Model: Active")
    st.metric("Detection Confidence", "94.2%")
    st.write("Last Identified:")
    st.code("Plastic Water Bottle")
