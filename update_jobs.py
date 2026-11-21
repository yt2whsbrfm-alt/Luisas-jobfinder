#!/usr/bin/env python3
"""Build a public jobs.json for Luisa JobFinder. No user data, keys or login.
Only documented public job posting endpoints. Standard-library-only.
"""
from __future__ import annotations
import datetime as dt
import email.utils
import html
import xml.etree.ElementTree as ET
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'jobs.json'
TODAY = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
MAX_JOB_AGE_DAYS = 365
AREA = (
 'kirchheim unter teck','kirchheim teck','dettingen unter teck','notzingen','holzmaden','ohmden',
 'weilheim an der teck','bissingen an der teck','neidlingen','lenningen','oberlenningen',
 'unterlenningen','owen','wendlingen','koengen','wernau','nuertingen','oberboihingen',
 'unterensingen','frickenhausen','grossbettlingen','neuffen','beuren','neckartailfingen',
 'neckartenzlingen','altenriet','schlaitdorf','reudern','aichtal','wolfschlugen','plochingen',
 'riederich','metzingen',
 'altbach','deizisau','reichenbach an der fils','hochdorf','hattenhofen','schlierbach',
 'ebersbach an der fils','albershausen','bad boll','zell unter aichelberg','aichelberg',
 'gammelshausen','denkendorf','baltmannsweiler','jesingen','oetlingen',
)
KEYWORDS = {
 'industrial engineer':32,'industrial engineering':32,'manufacturing engineer':29,
 'produktionsplan':34,'fertigungsplan':34,'arbeitsvorbereitung':34,
 'produktionssteuer':31,'fertigungssteuer':31,'prozessplan':30,'prozessoptimierung':30,
 'lean manager':29,'lean management':29,'lean specialist':28,'industrialization':26,
 'industrialisierung':26,'fertigungsoptimierung':31,'kapazitaetsplan':28,
 'montageplan':31,'materialfluss':24,'continuous improvement':25,'shopfloor':23,
 'produktionslogistik':23,'produktionsdisponent':23,'fertigungsdisponent':23,
 'production planner':34,'manufacturing planning':28,'production planning':29,
 'prozessingenieur':23,'prozesstechnolog':23,'operational excellence':25,
 'technischer projektkoordinator':22,'wertstrom':27,'re fa':18,'refa':24,
 'fertigungsorganisation':27,'operations planner':27,'projektkoordinator produktion':24,
 'value stream planner':33,'layoutplanner':29,'process engineer':27,
 'production steering':30,'quality engineer production':24,
}
SKILLS = {'montage','lean','fmea','apqp','planzeit','taktzeit','kapazitaet','line balancing',
 'wertstrom','pdca','shopfloor','arbeitsvorbereitung','prozess','vorrichtung','beschaffung',
 'fertigung','produktion','materialfluss','anlauf','inbetriebnahme','zeitaufnahme',
 'manufacturing','industrial engineering','automatisierung','maschinenbeschaffung','projekt'}
BLACKLIST = ('praktikum','internship','praktikant','werkstudent','working student','ausbildung',
 'duales studium','abschlussarbeit','bachelorarbeit','masterarbeit','premaster','pre-master',
 'ferienjob','minijob','student trainee','thesis','traineeprogramm','volontariat')


def norm(value: Any) -> str:
    text = str(value or '').casefold().replace('ä','ae').replace('ö','oe').replace('ü','ue').replace('ß','ss')
    text = unicodedata.normalize('NFKD',text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r'\s+', ' ', text.replace('ß','ss')).strip()


def strip_tags(value: Any) -> str:
    txt = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', str(value or ''), flags=re.I|re.S)
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', txt))).strip()


def location_ok(value: str) -> bool:
    loc = norm(value).replace('-', ' ')
    for name in AREA:
        nm = norm(name)
        if re.search(r'(?<!\w)'+re.escape(nm)+r'(?!\w)', loc):
            return True
    return False


def title_points(title: str) -> int:
    t = norm(title)
    return max((p for k, p in KEYWORDS.items() if k in t), default=0)


def fulltime_ok(title: str, text: str, employment: str='') -> bool:
    title_n = norm(title)
    if any(x in title_n for x in BLACKLIST): return False
    e = norm(employment)
    if ('part time' in e or 'part-time' in e or 'teilzeit' in e) and not ('full time' in e or 'full-time' in e or 'fulltime' in e or 'vollzeit' in e): return False
    if ('teilzeit' in title_n or 'part time' in title_n or 'part-time' in title_n) and not ('vollzeit' in title_n or 'full time' in title_n or 'full-time' in title_n or 'fulltime' in title_n): return False
    # If no explicit time appears, status is unknown; job is included but flagged accordingly.
    return True


def score_job(title: str, desc: str) -> tuple[int, list[str]]:
    title_n, d = norm(title), norm(desc)[:3500]
    tp = title_points(title_n)
    if not tp: return 0, []
    relevant = [s for s in SKILLS if s in d]
    score = min(96, 38 + tp + min(21, len(relevant)*3))
    reasons = ['Passender Tätigkeitsbereich']
    if relevant: reasons.append('Erfahrung passt: '+', '.join(sorted(relevant)[:3]))
    alternative = bool(re.search(r'(oder vergleichbare|vergleichbare qualifikation|alternativ|equivalent experience|gleichwertige berufserfahrung|gleichwertige qualifikation)', d))
    if not alternative and re.search(r'(abgeschlossenes masterstudium|masterabschluss|master.s degree)',d):
        return 0, []  # Luisa hat keinen nachgewiesenen Master; nicht als passend ausgeben.
    if not alternative and re.search(r'(abgeschlossenes studium|hochschulabschluss|university degree|bachelorabschluss)',d):
        score -= 18
        reasons.append('Studienanforderung im Original prüfen')
    if 'senior' in title_n or 'lead ' in title_n or 'leiter' in title_n: score -= 7
    return max(0, min(96,score)), reasons


def stamp(value: Any) -> int:
    if isinstance(value, (float,int)):
        return int(value * 1000 if value < 100000000000 else value)
    if not value: return 0
    try: return int(dt.datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()*1000)
    except (ValueError,TypeError):
        try: return int(email.utils.parsedate_to_datetime(str(value)).timestamp()*1000)
        except (ValueError, TypeError, IndexError): return 0


def get_text(url: str, *, tries: int=2) -> str:
    """Fetch small, publicly advertised job feeds; no authentication or HTML scraping."""
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'LuisaJobFinder/5.0 (public employer job feed reader)',
                'Accept': 'application/xml, application/rss+xml, application/json, text/xml, */*'})
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.read(10_000_000).decode('utf-8-sig', errors='replace')
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt + 1 < tries: time.sleep(1)
    raise RuntimeError(f'Öffentlicher Feed nicht erreichbar: {type(last).__name__}')


def xml_child_text(elem: ET.Element, tag: str) -> str:
    node = elem.find(tag)
    return ''.join(node.itertext()).strip() if node is not None else ''


def personio_feed(slug: str, company: str) -> tuple[list[dict], int]:
    """Official public Personio XML: {slug}.jobs.personio.de/xml?language=de"""
    raw = get_text(f'https://{slug}.jobs.personio.de/xml?language=de')
    root = ET.fromstring(raw)
    if root.tag != 'workzag-jobs':
        raise ValueError(f'Personio XML unerwartet: {root.tag}')
    results = []
    for item in root.findall('position'):
        title = xml_child_text(item, 'name')
        office = xml_child_text(item, 'office')
        # The exact employer's career site identifies IBO's "IBO Kirchheim" as Kirchheim unter Teck.
        if slug == 'ibo-tec' and norm(office) in ('ibo kirchheim', 'kirchheim'):
            office = 'Kirchheim unter Teck'
        if not location_ok(office) or not title_points(title): continue
        posting_id = xml_child_text(item, 'id')
        if not posting_id: continue
        desc = ' '.join(xml_child_text(d, 'value') for d in item.findall('./jobDescriptions/jobDescription'))
        schedule = xml_child_text(item, 'schedule')
        # Only unambiguously full-time Personio job ads are eligible.
        if norm(schedule) not in ('full-time','full time','vollzeit'): continue
        url = f'https://{slug}.jobs.personio.de/job/{urllib.parse.quote(posting_id, safe="")}?' + 'language=de'
        job = job_obj(f'{company} / Personio', title, company, office, url, desc,
                      xml_child_text(item, 'createdAt'), posting_id,
                      'full-time')
        if job: results.append(job)
    return results, 1


def ashby_neura_feed() -> tuple[list[dict], int]:
    """Ashby public Job Postings API – documented and keyless."""
    data = get_json('https://api.ashbyhq.com/posting-api/job-board/neura-robotics-gmbh')
    items = data.get('jobs')
    if not isinstance(items, list): raise ValueError('Ashby API-Format ungültig')
    out = []
    for item in items:
        if not item.get('isListed', True): continue
        if norm(item.get('employmentType')) not in ('fulltime', 'full-time', 'full time'): continue
        title = item.get('title') or ''
        if not title_points(title): continue
        primary = item.get('location') or ''
        addr = (item.get('address') or {}).get('postalAddress') or {}
        localities = [primary, addr.get('addressLocality') or '']
        localities.extend((x or {}).get('location','') for x in (item.get('secondaryLocations') or []))
        site = next((str(x) for x in localities if location_ok(str(x))), '')
        if not site: continue
        apply_url = item.get('jobUrl') or item.get('applyUrl') or ''
        if not apply_url.startswith('https://'): continue
        ident = str(item.get('id') or apply_url)
        desc = item.get('descriptionPlain') or item.get('descriptionHtml') or ''
        job = job_obj('NEURA Robotics / Ashby', title, 'NEURA Robotics', site, apply_url,
                      desc, item.get('publishedAt'), ident, 'FullTime')
        if job: out.append(job)
    return out, 1


def heller_rss_feed() -> tuple[list[dict], int]:
    """HELLER expressly publishes an 'Alle Stellenangebote' RSS feed."""
    raw = get_text('https://recruitment.heller.biz/handlers/offerRss.ashx?LCID=1031')
    root = ET.fromstring(raw)
    if root.tag not in ('rss','RDF'): raise ValueError('HELLER RSS-Format ungültig')
    out=[]
    for item in root.findall('.//item'):
        title=xml_child_text(item,'title')
        link=xml_child_text(item,'link')
        if link.startswith('http://recruitment.heller.biz/'):
            link='https://'+link[len('http://'):]
        desc=xml_child_text(item,'description')
        plain=strip_tags(desc)
        # Do not assume that every HELLER vacancy is in Nürtingen.
        if not re.search(r'nu[eü]rtingen|72622',norm(plain+' '+title)):
            continue
        if not re.search(r'vollzeit|full.time',norm(plain)):
            # RSS may omit working hours: this remains provisional in the UI.
            employment=''
        else:
            employment='Vollzeit'
        job=job_obj('HELLER / RSS',title,'HELLER', 'Nürtingen',link,plain,
                    xml_child_text(item,'pubDate'),link,employment)
        if job:out.append(job)
    return out,1


def get_json(url: str, *, tries: int=2) -> dict:
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent':'LuisaJobFinder/4.0 (public job feed reader)', 'Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=20) as response:
                raw=response.read(10_000_000)
            return json.loads(raw)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as ex:
            last = ex
            if attempt + 1 < tries: time.sleep(1.5)
    raise RuntimeError(f'HTTP/API-Abruf fehlgeschlagen: {type(last).__name__}')


def job_obj(source: str,title: str,company: str,location: str,url: str,description: str,created: Any,unique_id: str,employment: str='') -> dict|None:
    if not location_ok(location) or not fulltime_ok(title,description,employment):return None
    if not url.startswith('https://'): return None
    desc = strip_tags(description)[:2200]
    points,why=score_job(title,desc)
    if points < 55: return None
    time_ms=stamp(created)
    if time_ms and (time.time()*1000 - time_ms) > MAX_JOB_AGE_DAYS*86400*1000: return None
    known_hours = bool(re.search(r'vollzeit|full[ -]?time',norm(f'{title} {employment} {desc}')))
    if not known_hours: why.append('Arbeitszeit in Originalanzeige prüfen')
    return {'id':f'{source.lower().replace(" ", "-")}:{unique_id}', 'source':source,
      'title':str(title)[:170], 'company':str(company)[:100], 'location':str(location)[:180],
      'url':url, 'description':desc[:1600], 'created':time_ms, 'matchScore':points,
      'why':why, 'workingHoursConfirmed':known_hours}


def arbeitnow_feed() -> tuple[list[dict],int]:
    results=[];read=0
    for page in range(1,13):
        data=get_json('https://www.arbeitnow.com/api/job-board-api?page='+str(page))
        items=data.get('data',[])
        if not isinstance(items,list): raise ValueError('Arbeitnow-Format ungültig')
        read+=1
        for item in items:
            job=job_obj('Arbeitnow',item.get('title',''),item.get('company_name',''),
               item.get('location',''),item.get('url',''),item.get('description',''),
               item.get('created_at'),str(item.get('slug') or item.get('url')),
               ', '.join(item.get('job_types') or []))
            if job:results.append(job)
        if not data.get('next_page_url') or not items:break
    return results,read


def smartrecruiters_bosch() -> tuple[list[dict],int]:
    company='BoschGroup';results=[];read=0
    for offset in range(0,1600,100):
        url=f'https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100&offset={offset}'
        data=get_json(url)
        items=data.get('content')
        if not isinstance(items,list):raise ValueError('SmartRecruiters-Format ungültig')
        read+=1
        for item in items:
            title=item.get('name') or item.get('title') or ''
            city=(item.get('location') or {}).get('city') or ''
            if not (location_ok(city) and title_points(title)>0):continue
            ident=str(item.get('id') or '')
            if not ident:continue
            details={}
            try:
                details=get_json(f'https://api.smartrecruiters.com/v1/companies/{company}/postings/{urllib.parse.quote(ident)}',tries=1)
            except Exception:pass
            sections=((details.get('jobAd') or {}).get('sections') or {})
            description=' '.join(strip_tags(sections.get(k,{}).get('text','')) for k in ('jobDescription','qualifications','additionalInformation') if isinstance(sections.get(k),dict))
            desc=description or strip_tags(item.get('name',''))
            typ=(details.get('typeOfEmployment') or item.get('typeOfEmployment') or {})
            employment=typ.get('label','') if isinstance(typ,dict) else str(typ)
            # Public company posting URL based on the provider's documented job board.
            link=details.get('applyUrl') or item.get('applyUrl') or f'https://jobs.smartrecruiters.com/{company}/{ident}'
            job=job_obj('Bosch / SmartRecruiters',title,'Bosch Group',city,link,desc,
                      item.get('releasedDate') or details.get('releasedDate'),ident,employment)
            if job:results.append(job)
        if len(items)<100:break
    return results,read


def load_prior() -> dict:
    try: return json.loads(OUT.read_text(encoding='utf-8'))
    except (FileNotFoundError,ValueError):return {}


def main() -> int:
    prior=load_prior()
    all_jobs=[];sources=[]
    feeds = [
        ('Arbeitnow', arbeitnow_feed),
        ('Bosch / SmartRecruiters', smartrecruiters_bosch),
        ('NEURA Robotics / Ashby', ashby_neura_feed),
        ('IBO GmbH / Personio', lambda: personio_feed('ibo-tec', 'IBO GmbH')),
        ('Polymedics / Personio', lambda: personio_feed('polymedics-innovations-gmbh','Polymedics')),
        ('HELLER / RSS', heller_rss_feed),
    ]
    for label,fn in feeds:
        try:
            entries,pages=fn()
            all_jobs.extend(entries)
            sources.append({'name':label,'ok':True,'pages':pages,'matched':len(entries)})
        except Exception as err:
            sources.append({'name':label,'ok':False,'pages':0,'matched':0,'message':str(err)[:140]})
            print(f'WARN {label}: {err}',file=sys.stderr)
    if not any(s['ok'] for s in sources):
        print('Kein Feed erreichbar. Vorhandene jobs.json bleibt unverändert.',file=sys.stderr)
        return 1
    # Preserve previously found valid jobs for up to 10 days when one provider fails.
    good_sources={s['name'] for s in sources if s['ok']}
    prev_first={j.get('id'):j.get('firstFound') for j in prior.get('jobs',[])}
    previous=prior.get('jobs',[]) if isinstance(prior.get('jobs',[]),list) else []
    carried_ids=set()
    for p in previous:
        if p.get('source') in good_sources or not p.get('url','').startswith('https://'):continue
        dt_seen=stamp(p.get('lastSeen') or p.get('firstFound'))
        if dt_seen and time.time()*1000 - dt_seen < 10*86400*1000:
            all_jobs.append(p)
            carried_ids.add(p.get('id'))
    by_key={}
    for j in all_jobs:
        key='|'.join([norm(j['title']),norm(j['company']),norm(j['location'])])
        j['firstFound']=prev_first.get(j['id']) or TODAY
        if j.get('id') not in carried_ids: j['lastSeen']=TODAY
        if key not in by_key or j['matchScore']>by_key[key]['matchScore']:
            by_key[key]=j
    result=sorted(by_key.values(),key=lambda x:(-x['matchScore'],-int(x['created'] or 0)))[:200]
    out={'schema':2,'generatedAt':TODAY,'region':'Kirchheim unter Teck, grob 20 km (Ortsliste)',
         'profile':'Industrial Engineering / Produktionsplanung / Arbeitsvorbereitung',
         'sources':sources,'jobs':result}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Jobliste aktualisiert:',len(result),'Jobs;', [(s['name'],s['matched']) for s in sources])
    return 0

if __name__=='__main__':raise SystemExit(main())
