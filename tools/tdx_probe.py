#!/usr/bin/env python3
"""一次性檢查：TDX 金鑰是否設定、各項資料的大小與回應時間（只印統計，不印資料內容）。"""
import json, os, sys, time, gzip, datetime, urllib.request, urllib.parse

TDX = 'https://tdx.transportdata.tw'
cid, sec = os.environ.get('TDX_CLIENT_ID', '').strip(), os.environ.get('TDX_CLIENT_SECRET', '').strip()
print('TDX_CLIENT_ID：', '已設定' if cid else '未設定', '；TDX_CLIENT_SECRET：', '已設定' if sec else '未設定')
tok = None
if cid and sec:
    body = urllib.parse.urlencode({'grant_type': 'client_credentials', 'client_id': cid, 'client_secret': sec}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TDX + '/auth/realms/TDXConnect/protocol/openid-connect/token', data=body,
                                     headers={'Content-Type': 'application/x-www-form-urlencoded'}), timeout=30) as r:
            tok = json.load(r).get('access_token'); print('取得通行憑證：成功')
    except Exception as e:
        print('取得通行憑證：失敗', type(e).__name__, getattr(e, 'code', ''))
today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()
eps = [('v2', 'Air/GeneralSchedule/Domestic'), ('v2', 'Rail/THSR/GeneralTimetable'), ('v2', f'Rail/THSR/DailyTimetable/TrainDate/{today}'),
       ('v2', 'Rail/THSR/DailyTimetable/TrainDates'), ('v3', 'Rail/TRA/Station'), ('v3', f'Rail/TRA/DailyTrainTimetable/TrainDate/{today}'),
       ('v3', 'Rail/TRA/DailyTrainTimetable/TrainDates'), ('v3', f'Rail/TRA/DailyTrainTimetable/TrainDate/{today + datetime.timedelta(days=20)}')]
for ver, path in eps:
    url = f'{TDX}/api/basic/{ver}/{path}?$format=JSON'
    h = {'Accept-Encoding': 'gzip'}
    if tok: h['Authorization'] = 'Bearer ' + tok
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=120) as r:
            raw = r.read(); gz = len(raw)
            if r.headers.get('Content-Encoding') == 'gzip': raw = gzip.decompress(raw)
            j = json.loads(raw)
            n = len(j) if isinstance(j, list) else {k: (len(v) if isinstance(v, list) else v) for k, v in j.items() if not isinstance(v, (dict, str)) or k in ('UpdateTime', 'TrainDate')}
            print(f'{path}: {time.time()-t0:.1f} 秒，傳輸 {gz/1024:.0f} KB，解壓 {len(raw)/1024:.0f} KB，筆數 {n}')
            if 'TrainDates' in path: print('  可查日期：', str(j)[:300])
    except Exception as e:
        print(f'{path}: 失敗 {type(e).__name__} {getattr(e, "code", "")} （{time.time()-t0:.1f} 秒）')
    if tok: time.sleep(13)
