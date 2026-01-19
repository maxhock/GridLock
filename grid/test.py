import pandapower as pp
print("Lade Netz …")
net = pp.from_excel("data/input/kerber_landnetz_freileitung_1.xlsx")
print("Starte Power Flow …")
pp.runpp(net)
print("Power Flow erfolgreich!")