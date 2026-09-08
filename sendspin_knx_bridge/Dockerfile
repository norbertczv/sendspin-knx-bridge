FROM ghcr.io/home-assistant/base:latest
RUN apk add --no-cache python3 py3-pip
COPY requirements.txt /requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /requirements.txt
COPY run.sh /run.sh
COPY bridge.py /bridge.py
RUN chmod a+x /run.sh
CMD ["/run.sh"]
