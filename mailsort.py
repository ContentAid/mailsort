#!/usr/bin/env python3
"""Sort a Gmail inbox: Jev labels each new mail, noise gets archived, substance stays in the inbox.

Usage: mailsort.py [dry]   (dry = classify and print, change nothing)
Creds: ~/.config/mailsort/env (USER, PASS = Gmail app password). Jev key: ~/.config/typesafe/env.
Every mail handled gets a Jobs/* label, so a re-run only sees new mail.
"""
import email, imaplib, json, os, re, subprocess, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.header import decode_header, make_header

HOME = os.path.expanduser('~')
ENV = dict(l.split('=', 1) for l in open(HOME + '/.config/mailsort/env').read().split())
JEV_KEY = re.search(r'TYPESAFE_API_KEY=(\S+)', open(HOME + '/.config/typesafe/env').read())[1]
LOG = os.environ.get('MAILSORT_LOG', HOME + '/mail-substance.md')  # substance mail gets appended here
DRY = 'dry' in sys.argv

# label, archive?, Jev criterion
CATS = [
    ('Jobs/Substance', False, 'About a job the owner applied for, and the company wants them to act: interview invite or scheduling, '
                              'assessment, AI interview or take-home task, offer, a recruiter question, or a request for more info. '
                              'A plain "we received your application" is not this.'),
    ('Jobs/Applied', True, 'Automatic confirmation that a job application was received or submitted. No action needed.'),
    ('Jobs/Rejected', True, 'The company is not moving forward with the application, position filled or closed.'),
    ('Jobs/Alerts', True, 'Job alerts, job recommendations, newsletters, marketing, platform notifications, account or '
                          'verification codes for job sites, or any automated mail that needs nothing.'),
    ('Jobs/Other', False, 'Not about jobs at all: personal, business, banking or anything else.'),
]


def hdr(msg, k):
    return re.sub(r'\s+', ' ', str(make_header(decode_header(msg.get(k, '')))))


def text(msg):
    for p in msg.walk():
        if p.get_content_type() in ('text/plain', 'text/html'):
            try:
                b = p.get_payload(decode=True).decode(p.get_content_charset() or 'utf8', 'replace')
            except Exception:
                continue
            return re.sub(r'<style.*?</style>|<[^>]+>|&nbsp;|\s+', ' ', b, flags=re.S)[:1500]
    return ''


def jev(m):
    q = {'cat': {'type': 'choice', 'instructions': 'Which kind of email is this, for a job seeker sorting his inbox?',
                 'criteria': {f'o{i}': c[2] for i, c in enumerate(CATS)}}}
    body = json.dumps({'state': f"From: {m['from']}\nSubject: {m['subj']}\n\n{m['body']}", 'model': 'jev-latest', 'questions': q})
    req = urllib.request.Request('https://api.typesafe.ai/v1/systemone', body.encode(),
                                 {'Authorization': 'Bearer ' + JEV_KEY, 'Content-Type': 'application/json'})
    a = json.load(urllib.request.urlopen(req, timeout=60))['answers']['cat']
    return int(a['choice'][1:]), a.get('confidence', 0)


def main():
    im = imaplib.IMAP4_SSL('imap.gmail.com')
    im.login(ENV['USER'], ENV['PASS'])
    im.select('INBOX', readonly=DRY)
    _, d = im.uid('search', None, 'X-GM-RAW', '"in:inbox ' + ' '.join('-label:' + c[0].lower().replace('/', '-') for c in CATS) + '"')
    mails = []
    for uid in d[0].split():
        _, r = im.uid('fetch', uid, '(BODY.PEEK[])')
        msg = email.message_from_bytes(r[0][1])
        mails.append({'uid': uid, 'from': hdr(msg, 'From'), 'subj': hdr(msg, 'Subject'), 'date': msg.get('Date', ''), 'body': text(msg)})
    with ThreadPoolExecutor(16) as ex:
        res = list(ex.map(jev, mails))
    substance, counts = [], {}
    for m, (i, conf) in zip(mails, res):
        label, archive, _ = CATS[i]
        if conf < 0.5:  # unsure: keep it visible rather than bury it
            archive = False
        counts[label] = counts.get(label, 0) + 1
        if DRY:
            print(f'{label:15} {conf:.2f} {m["from"][:40]:40} {m["subj"][:70]}')
        else:
            im.uid('store', m['uid'], '+X-GM-LABELS', f'("{label}")')
            if archive:  # -X-GM-LABELS \Inbox is a no-op; delete+expunge in INBOX = archive (kept in All Mail)
                im.uid('store', m['uid'], '+FLAGS', '(\\Deleted)')
        if label == 'Jobs/Substance':
            substance.append(m)
    if not DRY:
        im.expunge()
    print(json.dumps(counts))
    if substance and not DRY:
        with open(LOG, 'a') as f:
            f.write(f'\n## {datetime.now():%Y-%m-%d %H:%M}\n' + ''.join(f'- {m["date"][:16]} | {m["from"]} | {m["subj"]}\n' for m in substance))
        subprocess.run(['notify-send', '-u', 'critical', f'{len(substance)} job mail need you',
                        '\n'.join(m['subj'][:60] for m in substance[:5])])


if __name__ == '__main__':
    main()
