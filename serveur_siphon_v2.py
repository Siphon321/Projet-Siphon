"""
PROJET SIPHON - SERVEUR DU PC (version 2)
-----------------------------------------
Role : recevoir les moyennes envoyees par la carte, les stocker dans une base
de donnees SQLite, et les afficher sur un site (derniere mesure, graphique,
historique, export CSV).

La carte envoie en HTTP POST, texte brut : T1;T2;T3;T4;T5
   exemple : 18.10;17.60;15.20;12.40;11.90     ("ERR" si un capteur n'a rien donne)
vers : http://ADRESSE_IP_DU_PC:8080/api/mesure

Installation (une fois) :  pip install flask
Lancement                :  python serveur_siphon_v2.py
Site                     :  http://localhost:8080
"""
import csv
import io
import socket
import sqlite3
import threading
from datetime import datetime

from flask import Flask, Response, jsonify, request

# ======================= REGLAGES =======================
BASE = "siphon.db"        # fichier de la base de donnees (cree automatiquement)
PORT_WEB = 8080           # port d'ecoute du serveur
NB_CAPTEURS = 5           # nombre de temperatures par message
# ========================================================

app = Flask(__name__)     # cree l'application web
verrou = threading.Lock() # evite que deux requetes ecrivent en meme temps dans la base


# ---------------------- BASE DE DONNEES ----------------------
def requete(sql, params=()):
    """Execute une requete SQL et renvoie les lignes. Les '?' sont remplaces par params."""
    with verrou:
        connexion = sqlite3.connect(BASE, timeout=10)    # ouvre (ou cree) le fichier de la base
        try:
            connexion.row_factory = sqlite3.Row          # permet d'ecrire ligne["t1"]
            lignes = connexion.execute(sql, params).fetchall()
            connexion.commit()                           # enregistre les changements
            return lignes
        finally:
            connexion.close()                            # ferme toujours la base


def init_base():
    """Cree la table si elle n'existe pas encore."""
    requete(
        """CREATE TABLE IF NOT EXISTS mesures(
               id    INTEGER PRIMARY KEY AUTOINCREMENT,  -- numero unique, automatique
               date  TEXT NOT NULL,                      -- date et heure de reception
               t1 REAL, t2 REAL, t3 REAL, t4 REAL, t5 REAL,  -- temperatures moyennes (NULL si ERR)
               brut  TEXT                                -- message recu, tel quel
           )"""
    )


# ---------------------- RECEPTION ----------------------
def decouper(message):
    """'18.10;17.60;ERR;12.40;11.90' -> [18.1, 17.6, None, 12.4, 11.9]
    Renvoie None si le message n'a pas exactement 5 morceaux."""
    morceaux = message.strip().split(";")
    if len(morceaux) != NB_CAPTEURS:
        return None
    valeurs = []
    for morceau in morceaux:
        try:
            valeurs.append(float(morceau))               # texte -> nombre
        except ValueError:
            valeurs.append(None)                         # "ERR" ou autre -> pas de valeur
    return valeurs


@app.route("/api/mesure", methods=["POST", "GET"])
def recevoir():
    """Adresse appelee par la carte. GET autorise pour tester depuis un navigateur :
    /api/mesure?msg=18.1;17.6;15.2;12.4;11.9"""
    message = request.get_data(as_text=True).strip()     # contenu du POST
    if not message:
        message = request.values.get("msg", "").strip()  # sinon : ?msg=... dans l'adresse
    if not message or len(message) > 300:
        return "message vide ou trop long", 400

    valeurs = decouper(message)
    if valeurs is None:
        return "format invalide : attendu T1;T2;T3;T4;T5", 400

    requete(
        "INSERT INTO mesures(date, t1, t2, t3, t4, t5, brut) VALUES (?,?,?,?,?,?,?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *valeurs, message),
    )
    print("Recu :", message)
    return "OK", 200                                     # 200 = bien recu


# ---------------------- DONNEES POUR LE SITE ----------------------
@app.route("/data")
def data():
    """Les 200 dernieres mesures, de la plus recente a la plus ancienne (JSON)."""
    lignes = requete("SELECT * FROM mesures ORDER BY id DESC LIMIT 200")
    total = requete("SELECT COUNT(*) AS n FROM mesures")[0]["n"]
    return jsonify(historique=[dict(l) for l in lignes], total=total)


@app.route("/export.csv")
def export():
    """Toute la base au format CSV (ouvrable dans Excel)."""
    sortie = io.StringIO()
    ecrivain = csv.writer(sortie, delimiter=";")
    ecrivain.writerow(["date", "T1", "T2", "T3", "T4", "T5"])
    for l in requete("SELECT * FROM mesures ORDER BY id"):
        ecrivain.writerow([l["date"], l["t1"], l["t2"], l["t3"], l["t4"], l["t5"]])
    return Response(
        sortie.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=siphon.csv"},
    )


@app.route("/")
def accueil():
    return PAGE


PAGE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Surveillance du siphon</title>
<style>
  body{font-family:system-ui,sans-serif;max-width:860px;margin:30px auto;padding:0 15px;color:#1f2937}
  h1{font-size:1.5rem} h2{font-size:1.1rem;margin-top:28px}
  #age{color:#475569;margin:6px 0 14px}
  .cartes{display:flex;flex-wrap:wrap;gap:10px}
  .carte{flex:1 1 120px;background:#f0f9ff;border-left:4px solid #0284c7;padding:10px 12px;border-radius:4px}
  .carte b{display:block;font-size:1.4rem}
  canvas{width:100%;max-width:820px;border:1px solid #e5e7eb;border-radius:4px}
  .legende span{margin-right:14px;font-size:.9rem}
  table{width:100%;border-collapse:collapse;font-size:.9rem}
  td,th{border-bottom:1px solid #e5e7eb;padding:5px;text-align:left}
  a{color:#0284c7}
</style></head><body>
<h1>Surveillance du siphon</h1>
<div id="age">Chargement...</div>
<div class="cartes" id="cartes"></div>
<h2>Évolution des températures</h2>
<div class="legende" id="legende"></div>
<canvas id="graph" width="820" height="300"></canvas>
<h2>Historique</h2>
<p><a href="/export.csv">Télécharger toute la base (CSV)</a> - <span id="total"></span> mesures enregistrées</p>
<table><thead><tr><th>Date</th><th>T1</th><th>T2</th><th>T3</th><th>T4</th><th>T5</th></tr></thead>
<tbody id="tab"></tbody></table>
<script>
const COULEURS=["#dc2626","#ea580c","#16a34a","#2563eb","#7c3aed"];
const NOMS=["T1","T2","T3","T4","T5"];
function fmt(v){return v===null||v===undefined ? "ERR" : v.toFixed(2);}
function age(d){
  const s=Math.round((Date.now()-new Date(d.replace(" ","T")))/1000);
  if(s<60) return s+" s";
  if(s<3600) return Math.floor(s/60)+" min";
  if(s<86400) return Math.floor(s/3600)+" h "+Math.floor((s%3600)/60)+" min";
  return Math.floor(s/86400)+" j";
}
function tracer(h){                       // h : du plus ancien au plus recent
  const c=document.getElementById("graph"), g=c.getContext("2d");
  const W=c.width, H=c.height, ml=45, mr=10, mt=10, mb=20;
  g.clearRect(0,0,W,H);
  const vals=[];
  h.forEach(l=>NOMS.forEach((n,i)=>{const v=l["t"+(i+1)]; if(v!==null) vals.push(v);}));
  if(!vals.length) return;
  let min=Math.min(...vals), max=Math.max(...vals);
  if(max-min<1){min-=0.5; max+=0.5;}
  g.font="11px sans-serif"; g.fillStyle="#374151"; g.strokeStyle="#e5e7eb";
  for(let k=0;k<=4;k++){                  // graduations horizontales
    const y=mt+(H-mt-mb)*k/4, v=max-(max-min)*k/4;
    g.beginPath(); g.moveTo(ml,y); g.lineTo(W-mr,y); g.stroke();
    g.fillText(v.toFixed(1),4,y+4);
  }
  NOMS.forEach((n,s)=>{                   // une courbe par capteur
    g.strokeStyle=COULEURS[s]; g.lineWidth=2; g.beginPath();
    let debut=true;
    h.forEach((l,i)=>{
      const v=l["t"+(s+1)]; if(v===null){debut=true; return;}
      const x=ml+(W-ml-mr)*(h.length>1 ? i/(h.length-1) : 0.5);
      const y=mt+(H-mt-mb)*(1-(v-min)/(max-min));
      if(debut){g.moveTo(x,y); debut=false;} else g.lineTo(x,y);
    });
    g.stroke();
  });
}
async function maj(){
  try{
    const r=await (await fetch("/data")).json();
    const h=r.historique;
    document.getElementById("total").textContent=r.total;
    document.getElementById("legende").innerHTML=NOMS.map((n,i)=>'<span style="color:'+COULEURS[i]+'">&#9632; '+n+'</span>').join("");
    if(!h.length){document.getElementById("age").textContent="Aucune mesure pour l'instant"; return;}
    document.getElementById("age").textContent="Dernière mesure reçue le "+h[0].date+" (il y a "+age(h[0].date)+")";
    document.getElementById("cartes").innerHTML=NOMS.map((n,i)=>
      '<div class="carte">'+n+'<b>'+fmt(h[0]["t"+(i+1)])+' °C</b></div>').join("");
    tracer(h.slice().reverse());
    document.getElementById("tab").innerHTML=h.slice(0,50).map(l=>
      "<tr><td>"+l.date+"</td><td>"+fmt(l.t1)+"</td><td>"+fmt(l.t2)+"</td><td>"+fmt(l.t3)+"</td><td>"+fmt(l.t4)+"</td><td>"+fmt(l.t5)+"</td></tr>").join("");
  }catch(e){
    document.getElementById("age").textContent="Le serveur ne répond pas";
  }
}
maj(); setInterval(maj,5000);
</script></body></html>"""


# ---------------------- DEMARRAGE ----------------------
if __name__ == "__main__":
    init_base()
    print("=== Serveur siphon demarre ===")
    print("Site sur ce PC : http://localhost:%d" % PORT_WEB)
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            print("Adresse a donner a la carte / au telephone : http://%s:%d" % (ip, PORT_WEB))
    except Exception:
        pass
    app.run(host="0.0.0.0", port=PORT_WEB)
