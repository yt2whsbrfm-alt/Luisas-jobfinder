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
import os
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
MAX_JOB_AGE_DAYS = 90
AREA = (
 'kirchheim unter teck','kirchheim teck','dettingen unter teck','notzingen','holzmaden','ohmden',
 'weilheim an der teck','bissingen an der teck','neidlingen','lenningen','oberlenningen',
 'unterlenningen','owen','wendlingen','koengen','wernau','nuertingen','oberboihingen',
 'unterensingen','frickenhausen','grossbettlingen','neuffen','beuren','neckartailfingen',
 'neckartenzlingen','altenriet','schlaitdorf','reudern','aichtal','wolfschlugen','plochingen',
 'riederich','metzingen','esslingen am neckar','esslingen','neuhausen auf den fildern',
 'aichwald','lichtenwald','bempflingen',
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
 'produktionskoordinator':30,'fertigungskoordinator':30,'arbeitsplaner':30,
 'arbeitsplanung':29,'produktionsorganis':28,'prozessmanager produktion':28,
 'continuous improvement manager':27,'produktionsdisposition':25,
 'materialdisponent':23,'fertigungsdisposition':24,'planung fertigung':25,
 'betriebsorganis':25,'production coordinator':29,'production scheduler':30,
 'manufacturing coordinator':28,'industrial engineerin':30,
 'fabrikplan':34,'werksplan':31,'werksstrukturplan':30,'fertigungsbereichsplan':28,
 'materialflussplan':32,'logistikplan':29,'produktionssystemplan':30,
 'fertigungsprozessplan':31,'layoutplan':29,'layoutengineer':27,
 'fabriklayout':28,'re fa-analyst':24,'industrial engineer expert':33,
 'process planner':29,'manufacturing planner':31,'production coordinator':29,
 'lean coordinator':26,'produktionscontroller':23,'industrial engineer specialist':30,
 'kvp manager':26,'kontinuierliche verbesser':25,
 'technische projektkoordination':25,'projektplanung fertigung':26,
 'logistikplanung':28,'planungsingenieur produktion':29,'prozesskoordinator':25,
 'supply chain planner':26,'npi coordinator':26,'operations excellence':26,
 'prozessmanagement produktion':27,'manufacturing specialist':25,
 'produktentstehungsprozess':22,'produktionsmanagement':25,
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
    """Conservative, explainable match, *not* a hiring probability.

    Avoid optimistic scores for roles with mandatory domain credentials
    (CNC programming, regulated lab professions, etc.). Generic coordination
    titles are only included when tasks demonstrate a production-planning role.
    """
    title_n, d = norm(title), norm(desc)[:6500]
    tp = title_points(title_n)
    domain = ('fertigung', 'produktion', 'manufactur', 'assembly', 'montage',
              'arbeitsvorbereitung', 'industrial engineering', 'produktionsplanung',
              'fertigungsplanung', 'materialfluss', 'werksplanung', 'fabrikplanung',
              'kapazitaet', 'line balancing', 'taktzeit', 'betriebsmittel')
    relevant = sorted(x for x in SKILLS if x in d)
    core_hits = sum(1 for x in domain if x in d)
    if not tp:
        wide_title = re.search(r'(projektkoordinator|project coordinator|project planner|'
             r'projektmanager|projektleitung|project manager|technical project|'
             r'supply chain|disponent|operations coordinator|operations manager|'
             r'prozessmanager|production control)', title_n)
        if not wide_title or core_hits < 3 or len(relevant) < 3:
            return 0, []
        tp = 20
    score = 36 + tp + min(18, len(relevant)*3)
    reasons = ['Tätigkeitsbereich passt zum Suchprofil']
    if relevant:
        reasons.append('Passende Erfahrung: '+', '.join(relevant[:3]))
    if not d:
        score -= 13
        reasons.append('Anforderungen nicht verfügbar – Originalanzeige prüfen')

    alternative = bool(re.search(r'(oder vergleichbare|vergleichbare qualifikation|'
        r'gleichwertige qualifikation|alternativ|equivalent (experience|qualification)|'
        r'berufserfahrung anstelle|techniker.{0,50}oder|fachwirt.{0,50}oder)', d))
    master = bool(re.search(r'(abgeschlossenes masterstudium|masterabschluss|master.s degree)',d))
    if master and not alternative:
        score -= 35
        reasons.append('Masterabschluss erwähnt – Zugangsbedingung prüfen')
    elif not alternative and re.search(r'(abgeschlossenes studium|hochschulabschluss|'
            r'university degree|bachelorabschluss|bachelor.s degree)',d):
        score -= 15
        reasons.append('Studienabschluss gefordert – gleichwertige Erfahrung prüfen')

    # Strict specialized qualification is a meaningful gap, not just a smaller bonus.
    cnc_title = bool(re.search(r'(cnc|cam.{0,12}programm|zerspan|dreh.{0,4}fraes|'
        r'fraesprogramm|mazatrol|heidenhain)',title_n))
    cnc_mandatory = bool(re.search(r'(cnc.{0,30}programm|mazatrol|fanuc|heidenhain|'
         r'siemens 840d|zerspanungsmechanik|cam.{0,15}programm)',d))
    if cnc_title and cnc_mandatory:
        score -= 47
        reasons.append('CNC-/CAM-Programmierung als Kernanforderung – derzeit keine belegte Erfahrung')
    elif cnc_mandatory and re.search(r'(erfahrung|kenntnisse|fundiert|sicher)',d):
        score -= 24
        reasons.append('Spezialkenntnisse CNC/CAM erforderlich – vor Bewerbung prüfen')

    other_specialist_title = re.search(r'(labor|chemie|biolog|medizinprodukt|'
         r'softwareentwick|embedded|steuerfach|elektrokonstrukt|hydraulik)',title_n)
    if other_specialist_title:
        score -= 30
        reasons.append('Fachspezialisierung möglicherweise nicht abgedeckt')
    if re.search(r'(senior|principal|director|head of|abteilungsleit|bereichsleit)',title_n):
        score -= 13
        reasons.append('Erhöhtes Erfahrungs-/Führungsniveau prüfen')
    return max(0, min(95,score)), reasons


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
                'User-Agent': 'LuisaJobFinder/8.0 (public employer job feed reader)',
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
        # Exclude explicitly part-time-only positions. Unknown hours are visible
        # with a warning rather than silently lost on an unfamiliar XML value.
        if not fulltime_ok(title, desc, schedule): continue
        url = f'https://{slug}.jobs.personio.de/job/{urllib.parse.quote(posting_id, safe="")}?' + 'language=de'
        job = job_obj(f'{company} / Personio', title, company, office, url, desc,
                      xml_child_text(item, 'createdAt'), posting_id,
                      schedule)
        if job: results.append(job)
    return results, 1


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
            req = urllib.request.Request(url, headers={'User-Agent':'LuisaJobFinder/8.0 (public job feed reader)', 'Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=20) as response:
                raw=response.read(10_000_000)
            return json.loads(raw)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as ex:
            last = ex
            if attempt + 1 < tries: time.sleep(1.5)
    raise RuntimeError(f'HTTP/API-Abruf fehlgeschlagen: {type(last).__name__}')


def excluded_employer(company: Any, url: Any = '') -> bool:
    """Exclude current employer across every source and any carried-over jobs."""
    name = norm(company)
    site = urllib.parse.urlsplit(str(url or '')).netloc.casefold()
    return (bool(re.search(r'(?<!\w)neura(?:\s+robotics)?(?!\w)', name))
            or 'neura-robotics.com' in site
            or 'neura-robotics.de' in site)


def job_obj(source: str,title: str,company: str,location: str,url: str,description: str,created: Any,unique_id: str,employment: str='') -> dict|None:
    if excluded_employer(company, url) or bool(re.search(r'(?<!\w)neura(?:\s+robotics)?(?!\w)',norm(title))): return None
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


# Broader nationwide aggregator. Free German key can be stored ONLY as a
# GitHub Actions secret JOOBLE_API_KEY, never in a public repository file.
JOOBLE_TERMS = (
    'Produktionsplaner', 'Fertigungsplaner', 'Arbeitsvorbereitung',
    'Industrial Engineer', 'Industrial Engineering', 'Manufacturing Engineer',
    'Fabrikplaner', 'Materialflussplaner', 'Logistikplaner',
    'Montageplanung', 'Fertigungssteuerung', 'Produktionssteuerung',
    'Prozessoptimierung', 'Prozessplaner', 'Lean Management',
    'Wertstromplanung', 'Arbeitsplanung', 'Produktionskoordinator',
    'Produktionslogistik', 'Fertigungsdisponent', 'Continuous Improvement',
    'Manufacturing Planner', 'Production Planner', 'Industrialization Engineer',
    'Kapazitätsplanung', 'REFA', 'KVP Manager',
    'Fertigungsorganisation', 'Projektkoordinator Produktion', 'Produktionsdisponent',
    'Logistikplanung', 'Technical Project Manager', 'Supply Chain Planner',
    'Operational Excellence', 'Prozesskoordinator', 'Planungsingenieur Produktion',
)



def jooble_feed() -> tuple[list[dict],int]:
    key=os.environ.get('JOOBLE_API_KEY','').strip()
    if not key:
        raise RuntimeError('Noch nicht aktiviert: kostenloser API-Schlüssel fehlt (GitHub Secret JOOBLE_API_KEY)')
    # 3 calls per run * 24 runs/day = 72 requests/day, under the documented 100/day free limit.
    # The 36 job families are covered over twelve scheduled hours; prior matches are retained.
    hour=int(dt.datetime.now(dt.timezone.utc).timestamp()//3600)
    start=(hour % (len(JOOBLE_TERMS)//3))*3
    terms=JOOBLE_TERMS[start:start+3]
    out=[];calls=0
    endpoint='https://de.jooble.org/api/'+urllib.parse.quote(key,safe='')
    for term in terms:
        body=json.dumps({'keywords':term, 'location':'Kirchheim unter Teck',
                         'radius':'26','page':'1','ResultOnPage':50,
                         'companysearch':False}).encode('utf-8')
        req=urllib.request.Request(endpoint,data=body,headers={
            'Accept':'application/json','Content-Type':'application/json',
            'User-Agent':'LuisaJobFinder/8.0 (private job finder)'},method='POST')
        try:
            with urllib.request.urlopen(req,timeout=25) as response:
                payload=json.load(response)
        except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError) as ex:
            # Avoid leaking key from the full URL into public Actions logs.
            raise RuntimeError('Jooble nicht erreichbar (HTTP/Netzwerk); API-Schlüssel und Limit prüfen') from None
        calls+=1
        jobs=payload.get('jobs')
        if not isinstance(jobs,list):
            raise ValueError('Unbekanntes Jooble-Antwortformat')
        for row in jobs:
            url=str(row.get('link') or '')
            obj=job_obj('Jooble',row.get('title',''),row.get('company',''),
                        row.get('location',''),url,row.get('snippet',''),
                        row.get('updated'),str(row.get('id') or url),
                        str(row.get('type') or ''))
            if obj:out.append(obj)
    return list({j['id']:j for j in out}.values()),calls


# An additional keyless feed, specializing in remote/hybrid opportunities.
# Only explicitly local workplaces are accepted by location_ok; external ad
# texts and application URLs are not extracted or republished.
def nomado_feed() -> tuple[list[dict],int]:
    url='https://api.nomado24.de/api/public/v2/jobs?applicant_region=DE&work_arrangement=hybrid&limit=100'
    out=[];count=0
    for _ in range(3):
        payload=get_json(url,tries=1)
        rows=payload.get('data',[])
        if not isinstance(rows,list):raise ValueError('Nomado24-Format ungültig')
        count+=1
        for row in rows:
            if not isinstance(row,dict):continue
            location=str(row.get('location') or '')
            obj=job_obj('Nomado24',row.get('title',''),row.get('companyName',''),
                        location,row.get('url',''),' '.join(map(str,row.get('tags') or [])),
                        row.get('publishedAt'),row.get('slug') or row.get('url',''),
                        'Vollzeit' if str(row.get('jobType','')).lower() in ('full-time','fulltime','vollzeit') else '')
            if obj: out.append(obj)
        meta=payload.get('meta') or {}
        cursor=meta.get('nextCursor') or payload.get('nextCursor')
        if not cursor or not meta.get('hasNext'):break
        url='https://api.nomado24.de/api/public/v2/jobs?cursor='+urllib.parse.quote(str(cursor),safe='')+'&limit=100'
    return out,count


def load_prior() -> dict:
    try: return json.loads(OUT.read_text(encoding='utf-8'))
    except (FileNotFoundError,ValueError):return {}


def main() -> int:
    prior=load_prior()
    all_jobs=[];sources=[]
    feeds = [
        ('Arbeitnow', arbeitnow_feed),
        ('Bosch / SmartRecruiters', smartrecruiters_bosch),
        ('IBO GmbH / Personio', lambda: personio_feed('ibo-tec', 'IBO GmbH')),
        ('Polymedics / Personio', lambda: personio_feed('polymedics-innovations-gmbh','Polymedics')),
        # Official public XML career boards verified for this region. Multi-location
        # jobs remain visibly labeled with their complete source location string.
        ('MR PLAN Group / Personio', lambda: personio_feed('mr-plan-gmbh','MR PLAN Group')),
        ('BRIGHT Consulting / Personio', lambda: personio_feed('bright-consulting','BRIGHT Consulting')),
        ('munerio / Personio', lambda: personio_feed('munerio','munerio consulting')),
        ('HELLER / RSS', heller_rss_feed),
        ('Nomado24', nomado_feed),
        *([('Jooble', jooble_feed)] if os.environ.get('JOOBLE_API_KEY') else []),
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
    # Keep rotating Jooble searches visible between runs (up to 30 hours),
    # and retain jobs of a temporarily failed provider for up to 2 days.
    good_sources={s['name'] for s in sources if s['ok']}
    prev_first={j.get('id'):j.get('firstFound') for j in prior.get('jobs',[]) if not excluded_employer(j.get('company',''),j.get('url',''))}
    previous=prior.get('jobs',[]) if isinstance(prior.get('jobs',[]),list) else []
    carried_ids=set()
    fresh_ids={j['id'] for j in all_jobs}
    for p in previous:
        if excluded_employer(p.get('company',''),p.get('url','')):continue
        if not p.get('url','').startswith('https://') or p.get('id') in fresh_ids:continue
        old_source=p.get('source')
        # Never carry results from a provider no longer configured (e.g. NEURA).
        if old_source not in {label for label, _ in feeds}:continue
        dt_seen=stamp(p.get('lastSeen') or p.get('firstFound'))
        max_age_hours=30 if old_source=='Jooble' and old_source in good_sources else 48
        if dt_seen and time.time()*1000-dt_seen < max_age_hours*3600*1000:
            # Important: legacy caches may contain old inflated scores.
            score, why = score_job(p.get('title',''),p.get('description',''))
            if score < 55: continue
            p['matchScore'] = score
            p['why'] = why + ([] if p.get('workingHoursConfirmed') else
                              ['Arbeitszeit in Originalanzeige prüfen'])
            all_jobs.append(p)
            carried_ids.add(p.get('id'))
    by_key={}
    for j in all_jobs:
        key='|'.join([norm(j['title']),norm(j['company']),norm(j['location'])])
        j['firstFound']=prev_first.get(j['id']) or TODAY
        if j.get('id') not in carried_ids: j['lastSeen']=TODAY
        if key not in by_key or j['matchScore']>by_key[key]['matchScore']:
            by_key[key]=j
    result=sorted(by_key.values(),key=lambda x:(-x['matchScore'],-int(x['created'] or 0)))[:500]
    out={'schema':2,'generatedAt':TODAY,'region':'Kirchheim unter Teck, grob 20 km (Ortsliste)',
         'profile':'V8: breiterer Branchenabgleich – Industrial Engineering, Produktion, Prozess-/Fabrik-/Materialflussplanung, technische Koordination',
         'sources':sources,'jobs':result}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Jobliste aktualisiert:',len(result),'Jobs;', [(s['name'],s['matched']) for s in sources])
    return 0

if __name__=='__main__':raise SystemExit(main())
