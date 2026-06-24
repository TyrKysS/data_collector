ARG BUILD_FROM
FROM $BUILD_FROM

RUN pip3 install --no-cache-dir flask==3.0.3 requests==2.31.0

COPY app/ /app/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
