FROM --platform=linux/amd64 daytonaio/langchain-open-swe:0.1.0
RUN python3 -m pip install -U langgraph-cli
RUN npm install -g @langchain/langgraph-cli
