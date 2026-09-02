FROM python:3.10

WORKDIR /app

RUN pip install cosim-toolbox pandapipes helics

COPY heat_federate.py .

CMD ["python", "heat_federate.py"]
