# Bot Telegram Modération + Parrainage — Railway

Projet Python prêt pour Railway avec interface admin à boutons.

## Fonctions incluses

- `/start` uniquement :
  - si l’ID est admin, le panel admin s’ouvre directement ;
  - sinon, l’utilisateur voit seulement le parcours de parrainage.
- Panel admin avec boutons inline.
- Info système : base branchée, groupe branché, vidéos `x/60`.
- Upload des 60 vidéos récompenses en envoyant les vidéos en privé au bot depuis un compte admin.
- Anti-liens : suppression + ban.
- Photo avec mention en légende : suppression + ban.
- Mots interdits : 1re fois mute 1 jour, 2e fois mute 7 jours, 3e fois ban.
- Horaires automatiques : ouverture 23h, fermeture 1h, timezone Europe/Paris.
- Message horaire quand le groupe est fermé : prochaine ouverture dans X heures.
- Anti-repost média sur 4 jours.
- Parrainage : lien privé généré par utilisateur, validation après 5 minutes dans le groupe.
- Récompenses : 1 invité = 1 vidéo, 5 = 10 vidéos, 30 = 50 vidéos, 40 = 60 vidéos.

## Important

Telegram ne permet pas toujours de lire la bio complète d’un utilisateur. Le code vérifie le `language_code` public quand disponible et refuse les profils qui ne semblent pas francophones. C’est un filtre linguistique, pas un filtre de nationalité.

Telegram ne permet pas non plus d’effacer tout l’historique arbitrairement. Le bot supprime les messages qu’il a vus et enregistrés pendant son fonctionnement, dans la limite de ce que l’API autorise.

## Variables Railway

Crée ces variables dans Railway :

```env
BOT_TOKEN=123456:ABCDEF
ADMIN_IDS=111111111,222222222
GROUP_ID=-1001234567890
DATABASE_PATH=/data/bot.sqlite3
TIMEZONE=Europe/Paris
WEBHOOK_URL=https://ton-app.up.railway.app
PORT=8080
```

### BOT_TOKEN

À récupérer avec BotFather.

### ADMIN_IDS

Liste des IDs Telegram admin séparés par des virgules.

### GROUP_ID

ID du groupe Telegram. Il commence souvent par `-100`.

### DATABASE_PATH

Pour Railway, ajoute un Volume et monte-le sur `/data`, puis utilise :

```env
DATABASE_PATH=/data/bot.sqlite3
```

Sans volume, la base SQLite peut être perdue lors d’un redéploiement.

### WEBHOOK_URL

Mets l’URL publique Railway de ton service, sans slash final.

Exemple :

```env
WEBHOOK_URL=https://mon-bot-production.up.railway.app
```

Si `WEBHOOK_URL` est vide, le bot démarre en polling, utile pour tester en local.

## Déploiement Railway

1. Crée un bot avec BotFather.
2. Désactive la confidentialité du bot dans BotFather : `Bot Settings > Group Privacy > Turn off`.
3. Ajoute le bot dans ton groupe.
4. Donne au bot les droits admin : supprimer messages, bannir, restreindre membres, gérer les invitations.
5. Mets le code sur GitHub.
6. Sur Railway : New Project > Deploy from GitHub repo.
7. Ajoute les variables d’environnement.
8. Ajoute un Volume monté sur `/data`.
9. Déploie.
10. En privé avec le bot, un admin envoie `/start` pour ouvrir le panel.

## Utilisation admin

### Ouvrir le panel

En privé avec le bot :

```text
/start
```

Si ton ID est dans `ADMIN_IDS`, le panel admin s’affiche.

### Ajouter une vidéo récompense

Envoie simplement une vidéo au bot en privé depuis un compte admin.

Le bot répond :

```text
✅ Vidéo ajoutée : 1/60
```

### Ajouter un mot interdit

En privé au bot :

```text
+motinterdit
```

### Supprimer un mot interdit

```text
-motinterdit
```

### Voir la liste

```text
liste
```

## Fichiers

- `bot.py` : code principal.
- `requirements.txt` : dépendances Python.
- `Procfile` : commande Railway.
- `.env.example` : exemple de configuration.
- `runtime.txt` : version Python.
