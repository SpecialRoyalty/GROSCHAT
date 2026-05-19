# Telegram Railway Bot - FINAL_COMPLETE_V12

## Nouveautés V11

- Participation ON/OFF : avant d'écrire, un membre doit envoyer une photo ou vidéo nouvelle.
- Participation validée à vie après 1 média nouveau.
- Si média déjà vu : pas de validation, suppression et message d'avertissement.
- Relance non-participants par lots de 20 à l'ouverture.
- Kick des non-participants après 3 jours sans participation.
- Hash média SHA256 téléchargé depuis Telegram, plus solide que file_unique_id.
- Anti-repost étendu à 10 jours via hash.
- Ban hash : l'admin envoie une photo/vidéo au bot, son hash est banni.
- Si hash banni republié : ban direct + message automatique.
- Règles auto toutes les 15 minutes quand le groupe est ouvert, l'ancien message règles est supprimé.
- Bilan de sanctions toutes les 20 minutes quand le groupe est ouvert.
- Silent mode : sanctions visibles ON/OFF.
- Mode RAID ON/OFF : nouveaux membres mute et médias bloqués.
- Broadcast groupe depuis le panel admin.
- Les commandes / dans le groupe sont supprimées ; récidive = mute 1 mois.
- Ajout de bot interdit.
- Admins Telegram / owner / ADMIN_IDS exemptés des liens et transferts.
- Non-admins : liens et transferts = ban.
- Récompenses remplacées par 4 liens hébergeur : 1, 10, 50, 60.

## Variables Railway

BOT_TOKEN=TON_NOUVEAU_TOKEN
BOT_USERNAME=TonBotUsername
ADMIN_IDS=5296696302
GROUP_ID=-1003812221754
DATABASE_URL=${{Postgres.DATABASE_URL}}
TIMEZONE=Europe/Paris
WEBHOOK_URL=https://TONAPP.up.railway.app
PORT=8080

## Vérification

Dans les logs Railway :

STARTING FINAL_COMPLETE_V12

## Droits Telegram requis

Le bot doit être admin avec :
- supprimer messages
- bannir utilisateurs
- restreindre utilisateurs
- gérer permissions
- inviter via lien

## Base ancienne

Si tu as des erreurs de colonnes à cause d'une ancienne base, supprime seulement les tables concernées :

pending_joins, referrals, referral_links, user_rewards, referrer_abuse, reward_links, participants, banned_hashes, media_fingerprints, danger_scores

Ne supprime pas settings si tu veux garder les réglages.


## V12 - Correction compte à rebours

Ouverture :
- > 1h : message par heure.
- 1h avant : Prochaine ouverture dans 1 heure.
- 30 min avant : Prochaine ouverture dans 30 minutes.
- 10 min avant : Ouverture dans 10 minutes.
- 5,4,3,2,1 min avant : compte à rebours minute par minute.
- puis ouverture automatique.

Fermeture :
- 30 min avant : avertissement.
- 15 min avant : avertissement.
- 5,4,3,2,1 min avant : compte à rebours minute par minute.
- puis fermeture + suppression de session.
