#!/usr/bin/env python3
"""定時預抓 TDX 的航班、高鐵、台鐵資料，存成網頁可以直接讀的小檔案（發布到 tdx-data 分支）。

網頁查詢時先讀這裡的檔案；沒有該日期或檔案太舊時，才改向 TDX 即時查詢。
金鑰從環境變數 TDX_CLIENT_ID / TDX_CLIENT_SECRET 讀取（GitHub Actions secrets），不寫進任何檔案。

輸出（out 目錄）：
  manifest.json          {at, air, thsrGeneral, traStations, thsr:[日期], tra:[日期]}
  air.json               國內航線定期班表（TDX 原始欄位）
  thsr-general.json      高鐵定期時刻表
  thsr/YYYY-MM-DD.json   高鐵每日時刻表
  tra-stations.json      台鐵車站
  tra/YYYY-MM-DD.json    台鐵每日時刻表（精簡：[車次資訊, [[站碼, 到站, 離站], ...]]）
  cctv/<來源>.json       道路攝影機清單（Freeway、Highway、City-<縣市>；只留網頁用到的欄位）
"""
import json, os, sys, time, gzip, datetime, urllib.request, urllib.parse, urllib.error

TDX = 'https://tdx.transportdata.tw'
TW = datetime.timezone(datetime.timedelta(hours=8))
GAP = 12.5          # 一般會員每分鐘最多 5 次
THSR_DAYS = 14      # 高鐵每日時刻表預抓天數
TRA_DAYS = 8        # 台鐵每日時刻表預抓天數（今天起）
CCTV_SRC = ['Freeway', 'Highway'] + ['City/' + c for c in (
    'Taipei NewTaipei Keelung Taoyuan Hsinchu HsinchuCounty MiaoliCounty Taichung ChanghuaCounty NantouCounty YunlinCounty '
    'Chiayi ChiayiCounty Tainan Kaohsiung PingtungCounty YilanCounty HualienCounty TaitungCounty PenghuCounty KinmenCounty LienchiangCounty').split()]
CCTV_F = ('CCTVID', 'PositionLat', 'PositionLon', 'RoadName', 'SurveillanceDescription', 'RoadDirection', 'LocationMile', 'RoadSection', 'VideoStreamURL', 'VideoImageURL')
_last = [0.0]


def token():
    cid, sec = os.environ.get('TDX_CLIENT_ID', '').strip(), os.environ.get('TDX_CLIENT_SECRET', '').strip()
    if not cid or not sec:
        sys.exit('沒有設定 TDX_CLIENT_ID / TDX_CLIENT_SECRET')
    body = urllib.parse.urlencode({'grant_type': 'client_credentials', 'client_id': cid, 'client_secret': sec}).encode()
    req = urllib.request.Request(TDX + '/auth/realms/TDXConnect/protocol/openid-connect/token', data=body,
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)['access_token']


def get(tok, ver, path, empty_codes=(404,)):
    for attempt in range(3):
        wait = _last[0] + GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        url = f'{TDX}/api/basic/{ver}/{path}?$format=JSON' + ('&$top=5000' if ver == 'v3' else '')
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + tok, 'Accept-Encoding': 'gzip'})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                if r.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(61); continue
            if e.code in empty_codes:
                return None
            raise
    return None


def arr(j, *keys):
    if isinstance(j, list):
        return j
    for k in keys:
        if isinstance(j, dict) and isinstance(j.get(k), list):
            return j[k]
    return []


def zh(v):
    return {'Zh_tw': v.get('Zh_tw', '')} if isinstance(v, dict) else v


def tra_compact(trains):
    out = []
    for t in trains:
        info = t.get('TrainInfo') or {}
        keep = {k: (zh(info[k]) if k.endswith('Name') or k == 'Note' else info[k])
                for k in ('TrainNo', 'TrainTypeName', 'StartingStationName', 'EndingStationName', 'TripLine', 'Note') if k in info}
        stops = sorted(t.get('StopTimes') or [], key=lambda s: int(s.get('StopSequence') or 0))
        out.append([keep, [[str(s.get('StationID', '')), s.get('ArrivalTime') or '', s.get('DepartureTime') or ''] for s in stops]])
    return out


def dump(path, data):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))


def main(out):
    tok = token()
    today = datetime.datetime.now(TW).date()
    man = {'at': datetime.datetime.now(TW).isoformat(timespec='seconds'), 'thsr': [], 'tra': []}
    errs = []

    def step(name, fn):
        try:
            fn()
        except Exception as e:  # 單項失敗不影響其他項目
            errs.append(f'{name}: {type(e).__name__} {getattr(e, "code", "")}')

    def air():
        a = arr(get(tok, 'v2', 'Air/GeneralSchedule/Domestic'))
        if len(a) < 50: raise ValueError('航班資料太少')
        dump(f'{out}/air.json', a); man['air'] = len(a)

    def thsr_general():
        a = arr(get(tok, 'v2', 'Rail/THSR/GeneralTimetable'))
        if len(a) < 20: raise ValueError('高鐵定期時刻表太少')
        dump(f'{out}/thsr-general.json', a); man['thsrGeneral'] = len(a)

    def thsr_days():
        dates = arr(get(tok, 'v2', 'Rail/THSR/DailyTimetable/TrainDates'), 'TrainDates')
        for d in [x for x in dates if str(today) <= x <= str(today + datetime.timedelta(days=THSR_DAYS - 1))]:
            a = arr(get(tok, 'v2', f'Rail/THSR/DailyTimetable/TrainDate/{d}'))
            if a:
                dump(f'{out}/thsr/{d}.json', a); man['thsr'].append(d)

    def tra_stations():
        a = arr(get(tok, 'v3', 'Rail/TRA/Station'), 'Stations')
        if len(a) < 100: raise ValueError('台鐵車站太少')
        dump(f'{out}/tra-stations.json', a); man['traStations'] = len(a)

    def tra_days():
        for i in range(TRA_DAYS):
            d = str(today + datetime.timedelta(days=i))
            a = arr(get(tok, 'v3', f'Rail/TRA/DailyTrainTimetable/TrainDate/{d}'), 'TrainTimetables')
            if len(a) > 100:
                dump(f'{out}/tra/{d}.json', tra_compact(a)); man['tra'].append(d)

    def cctv():
        import re
        man['cctv'] = {}
        odd = []
        for src in CCTV_SRC:
            try:   # 縣市沒有資料時 TDX 會回 404 或 400：當作沒有攝影機
                j = get(tok, 'v2', f'Road/Traffic/CCTV/{src}', empty_codes=(404, 400) if src.startswith('City/') else (404,))
            except Exception as e:
                errs.append(f'攝影機 {src}: {type(e).__name__} {getattr(e, "code", "")}'); continue
            a = [{k: x[k] for k in CCTV_F if k in x} for x in arr(j, 'CCTVs')] if j is not None else []
            dump(f'{out}/cctv/{src.replace("/", "-")}.json', a); man['cctv'][src] = len(a)
            # 檢查影像網址是否帶有會過期的通行碼（只印統計，不印網址）
            n = sum(1 for x in a if re.search(r'(token|expire|exp=|sig=|signature|auth|key=)', (x.get('VideoStreamURL') or '') + (x.get('VideoImageURL') or ''), re.I))
            if n: odd.append(f'{src} {n}/{len(a)}')
        print('攝影機網址疑似含通行碼：' + ('、'.join(odd) if odd else '沒有'))

    for name, fn in (('航班', air), ('高鐵定期', thsr_general), ('高鐵每日', thsr_days), ('台鐵車站', tra_stations), ('台鐵每日', tra_days), ('攝影機', cctv)):
        step(name, fn)
    if not any(k in man for k in ('air', 'thsrGeneral', 'traStations')) and not man['thsr'] and not man['tra']:
        sys.exit('全部失敗，不發布：' + '；'.join(errs))
    dump(f'{out}/manifest.json', man)
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(out) for f in fs)
    print(f"完成：航班 {man.get('air', '失敗')}、高鐵定期 {man.get('thsrGeneral', '失敗')}、高鐵每日 {len(man['thsr'])} 天、"
          f"台鐵車站 {man.get('traStations', '失敗')}、台鐵每日 {len(man['tra'])} 天、攝影機 {len(man.get('cctv', {}))} 份 {sum(man.get('cctv', {}).values())} 支；共 {size/1024:.0f} KB")
    if errs:
        print('部分失敗：' + '；'.join(errs))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'out')
