#!/usr/bin/env python3
"""每天抓一次 twipcam 的攝影機清單，只留 TDX 沒有的類別，存成 cams-extra.json 給網頁讀。

資料來源：twipcam 台灣即時影像監視器 https://www.twipcam.com/api/document
授權：CC BY 3.0 TW（網頁上會標示出處）。twipcam 不允許其他網站直接讀取，所以先存一份放在這裡。

輸出格式：{"at": 抓取時間, "src": 來源, "cams": [[類別, id, 緯度, 經度, 名稱, 影像]]}
  類別 s=景點直播 w=水利署水情 p=科學園區
  影像 "" 表示 twipcam 的快照 https://c01.twipcam.com/cam/snapshot/{id}.jpg；"yt:xxx" 表示 YouTube 直播；其他為原始網址
"""
import json, re, sys, urllib.request, datetime

URL = 'https://www.twipcam.com/api/v1/cam-list.json'
SNAP = 'https://c01.twipcam.com/cam/snapshot/{}.jpg'
PARKS = ('hsp', 'stsp', 'ctsp')
SKIP_NAME = re.compile(r'進香|遶境|新聞|直播頻道|PTS Live|公視|NHK')


def cat(c):
    pre = re.match(r'[a-z]*', c['id']).group(0)
    if pre == 'wra':
        return 'w'
    if pre in PARKS:
        return 'p'
    if 'i.ytimg.com/vi/' in c['cam_url'] and not SKIP_NAME.search(c['name']):
        return 's'
    return None


def main(out):
    req = urllib.request.Request(URL, headers={'User-Agent': 'guard-route-workbench cam list (daily)'})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    if not isinstance(data, list) or len(data) < 1000:
        sys.exit(f'清單看起來不對（{type(data).__name__}，{len(data) if hasattr(data, "__len__") else "?"} 筆），不更新')
    cams = []
    for c in data:
        try:
            k = cat(c)
            lat, lon = round(float(c['lat']), 5), round(float(c['lon']), 5)
        except (KeyError, TypeError, ValueError):
            continue
        if not k or not lat or not lon or not (20 < lat < 27 and 117 < lon < 123):
            continue
        url = str(c.get('cam_url') or '')
        m = re.search(r'i\.ytimg\.com/vi/([\w-]{6,20})/', url)
        if m:
            url = 'yt:' + m.group(1)
        elif url == SNAP.format(c['id']):
            url = ''
        name = re.sub(r'^水利署水資源\s*', '', str(c.get('name') or '').strip())
        cams.append([k, str(c['id']), lat, lon, name, url])
    cams.sort(key=lambda x: (x[0], x[1]))
    if len(cams) < 500:
        sys.exit(f'只剩 {len(cams)} 支，可能是來源格式改了，不更新')
    try:
        old = json.load(open(out, encoding='utf8'))
        if old.get('cams') == cams:
            print('沒有變動'); return
    except (OSError, ValueError):
        pass
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    with open(out, 'w', encoding='utf8') as f:
        json.dump({'at': now, 'src': 'twipcam 台灣即時影像監視器（CC BY 3.0 TW）', 'cams': cams}, f, ensure_ascii=False, separators=(',', ':'))
    n = {k: sum(1 for c in cams if c[0] == k) for k in 'swp'}
    print(f'已更新：景點 {n["s"]}、水情 {n["w"]}、園區 {n["p"]}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'cams-extra.json')
