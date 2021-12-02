import requests, urllib, os, firebase_admin, secrets, datetime, time
from flask import Flask, redirect, request, make_response
from firebase_admin import firestore, credentials

# 設定を環境変数から取得します(env.yamlファイルに記述)
client_id = os.environ.get('client_id', '')
client_secret = os.environ.get('client_secret', '')
freee_webapp_auth_url = os.environ.get('freee_webapp_auth_url', '')
mainpage_url = os.environ.get('mainpage_url', '')
redirect_url = f"{mainpage_url}?page=redirect"
session_expire_minutes = int(os.environ.get('session_expire_minutes', '10'))
cookie_name = os.environ.get('cookie_name', '')
Firestore_session_collection_name = os.environ.get('Firestore_session_collection_name', '')

# シングルトンのFireStoreドライバー取得関数
def get_firestore_instance():
    if not firebase_admin._apps:
        cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred)

    return firestore.client()

# クッキーやトークンをFireStoreに保存する関数
def store_cookie_in_Firestore(cookie_value, access_token, access_token_expires_at_unixtime, refresh_token, scope):

    try:
        db = get_firestore_instance()
        data = {
            u'access_token': access_token,
            u'access_token_expires_at_unixtime': access_token_expires_at_unixtime,
            u'refresh_token': refresh_token,
            u'scope' : scope
        }

        db.collection(Firestore_session_collection_name).document(cookie_value).set(data)
    except Exception as ex:
        raise Exception(f"store_cookie_in_Firestore() error.\n{ex}\nlocal variables:{locals()}")

# ユーザーのクッキーを、FireStoreのデータを使って検証し、有効な場合はアクセストークンを返す関数
def validate_cookie_and_get_access_token(cookie):

    db = get_firestore_instance()
    doc_ref = db.collection(Firestore_session_collection_name).document(cookie)
    doc = doc_ref.get()

    # クッキーがそもそも不正の場合はNoneを返す
    if not doc.exists:
        print('No cookie entry in Firestore')
        return None

    # クッキーが存在するが、アクセストークンの有効期限が切れていた場合は
    # リフレッシュトークンを使ってアクセストークンを再取得し、FireStoreのデータを更新
    if doc.to_dict()["access_token_expires_at_unixtime"] <= datetime.datetime.now().timestamp():
        refresh_token = doc.to_dict()["refresh_token"]
        data = get_token_and_store_toFS(refresh_token=refresh_token, cookie=cookie)
        cookie_value = data[0]
        access_token = data[1]
    else:
        access_token = doc.to_dict()["access_token"]

    return access_token

# トークンをfreee APIから取得し、FireStoreに保存
def get_token_and_store_toFS(auth_code=None, refresh_token=None, cookie=None):

    # freee APIへのリクエストボディを作る
    token_url = "https://accounts.secure.freee.co.jp/public_api/token"

    # トークンを初めてリクエストする場合（freeeの認可ページ経由）
    if refresh_token is None:
        params = {
            "grant_type":"authorization_code",
            "code":auth_code,
            "redirect_uri":redirect_url,
            "client_id":client_id,
            "client_secret":client_secret
        }

    # リフレッシュトークンでアクセストークンをリクエストする場合
    else:
        params = {
            "grant_type":"refresh_token",
            "refresh_token":refresh_token,
            "redirect_uri":redirect_url,
            "client_id":client_id,
            "client_secret":client_secret
        }

    # freee APIにリクエストし、Cookieを生成、トークンをFireStoreに保存
    encoded_params = urllib.parse.urlencode(params)
    response = requests.post(token_url, data=params, headers={"Content-Type":"application/x-www-form-urlencoded"})
    if response.status_code != requests.codes.ok:
        print(f"Error in getting token.")
        print(response.text)
        return "error"
    else:
        response_dict = response.json()
        access_token = response_dict["access_token"]
        refresh_token = response_dict["refresh_token"]
        scope =  response_dict["scope"]

        expires_in = response_dict["expires_in"] # ex.) 86400(sec): 24hours
        created_at = response_dict["created_at"] # unix time ex.)1638325888
        access_token_expires_at_unixtime = created_at + expires_in

        # Generate random value cookie
        if cookie is None:
            cookie = secrets.token_urlsafe(32)

        # Store cookie in Firestore
        try:
            store_cookie_in_Firestore(cookie, access_token, access_token_expires_at_unixtime, refresh_token, scope)
            return cookie,access_token
        except Exception as ex:
            print(f"Error in get_token_and_store_toFS(): {ex}")
            return "申し訳ございませんが、技術的問題が起きているようです。しばらくお待ちいただき、再度アクセスして下さい。"

def test_api_call(access_token):

    simplest_api_url = "https://api.freee.co.jp/api/1/companies"
    headers = {'Authorization': 'Bearer {}'.format(access_token)}
    response = requests.get(simplest_api_url, headers=headers)
    return response

# Cloud Functionから最初に呼ばれる関数
def mainpage(request):

    # GETパラメーターのpageによって、処理を変える
    nav_page = request.args.get('page', default='main')

    # page=mainもしくはパラメーターなしの場合はこちら
    if nav_page == "main":

        # セッションクッキーがなければ、freeeのOIDC認可ページにリダイレクトさせる
        if request.cookies.get(cookie_name, None) is None:
            print("no session cookie")
            response = make_response(redirect(freee_webapp_auth_url))
            return response

        # Get access token by session cookie
        access_token = validate_cookie_and_get_access_token(request.cookies[cookie_name])
        if access_token is None:
            print("Error in access token")
            return f"アクセストークンを取得できませんでした。リダイレクトURLなどの設定を再確認してください。"

        # Get 1 simple API request's response and show it in GUI
        simplest_api_response = test_api_call(access_token)
        return f"アクセストークン:{access_token} <br>https://api.freee.co.jp/api/1/companiesへのAPIコール出力<br>{simplest_api_response} <br>{simplest_api_response.text}"

    # page=redirectの場合はこちら。freeeからのOIDCリダイレクト用
    elif nav_page == "redirect":
        # Check OIDC challenge code exist in the request
        if 'code' in request.args:
            auth_code = request.args.get('code')
            print(f"auth code retrieved successfully")
        else:
            print("OIDC rediret request does not have 'code' parameter")
            return "申し訳ございませんが、こちらのURLへの直接のアクセスはご遠慮下さい。"

        data = get_token_and_store_toFS(auth_code=auth_code)
        cookie = data[0]
        expire_date = datetime.datetime.now() + datetime.timedelta(minutes=session_expire_minutes)

        # back to main page, with setting cookie
        response = make_response(redirect(mainpage_url))
        response.set_cookie(cookie_name, value=cookie, expires=expire_date) 
        return response


    else:
        print("error")
        return "error"
