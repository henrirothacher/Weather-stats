import streamlit as st

st.title("Hello, Streamlit!")
st.write("Welcome to your first Streamlit app.")

if st.button("Say hello"):
    st.success("Hello, world!")
