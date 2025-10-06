import helics as h
import time

# Create broker (ZMQ, waiting for 2 federates)
broker = h.helicsCreateBroker("zmq", "mainbroker", "--federates=1")

print("Broker created. Waiting for federates to connect...")

# Keep broker running until disconnected
while h.helicsBrokerIsConnected(broker):
    time.sleep(1)

print("Broker disconnected.")

h.helicsCloseLibrary()
