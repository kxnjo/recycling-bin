import streamlit as st

st.set_page_config(page_title="System Logs", layout="wide", page_icon="📋")

st.title("System Logs")
st.markdown("Historical data received from the MQTT broker.")

if 'history' in st.session_state and not st.session_state.history.empty:
    # Display newest entries at the top
    st.dataframe(st.session_state.history.iloc[::-1], use_container_width=True)
    
    if st.button("Clear Logs"):
        # Reset the dataframe keeping the columns
        st.session_state.history = st.session_state.history.iloc[0:0]
        st.rerun()
else:
    st.info("No data received from MQTT broker yet. Waiting for transmission...")