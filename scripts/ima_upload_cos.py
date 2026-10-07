# -*- coding: utf-8 -*-
"""ima 知识库本地文件上传参考实现（create_media → COS PUT → add_knowledge 三步中的第二步）。

用法：
    export IMA_SECRET_ID=...      # create_media 返回的 cos_credential.secret_id
    export IMA_SECRET_KEY=...     # cos_credential.secret_key
    export IMA_TOKEN=...          # cos_credential.token
    python ima_upload_cos.py <本地文件> <cos_key> <bucket_name> <region>

关键经验（踩坑记录，2026-10-07 实测）：
- 不要用官方 SDK 的 Domain 模式（签名主机不对，必 403）；
- 不要用凭证里的 custom_domain（CDN 只读，PUT 返回 403 / InvalidAccessKeyId）；
- 正确做法是原始 PUT 到标准端点 https://<bucket>.cos.<region>.myqcloud.com/<cos_key>，
  手工计算 COS 签名（q-sign-algorithm=sha1，q-header-list 只签 host），并带
  x-cos-security-token 头携带临时 token → 返回 200。

签名算法：
    SignKey       = HMAC-SHA1(SecretKey, KeyTime)
    HttpString    = "put\n" + URI + "\n\n" + "host=<host>\n"
    StringToSign  = "sha1\n" + KeyTime + "\n" + SHA1(HttpString) + "\n"
    Signature     = HMAC-SHA1(SignKey, StringToSign)
"""
import hashlib
import hmac
import os
import sys
import time
import urllib.request


def cos_put(local_path: str, cos_key: str, bucket: str, region: str) -> int:
    secret_id = os.environ["IMA_SECRET_ID"]
    secret_key = os.environ["IMA_SECRET_KEY"]
    token = os.environ["IMA_TOKEN"]
    host = "%s.cos.%s.myqcloud.com" % (bucket, region)

    start = int(time.time()) - 60
    key_time = "%d;%d" % (start, start + 3600)
    sign_key = hmac.new(secret_key.encode(), key_time.encode(), hashlib.sha1).hexdigest()
    http_string = "put\n/" + cos_key + "\n\n" + "host=" + host + "\n"
    string_to_sign = "sha1\n" + key_time + "\n" + hashlib.sha1(http_string.encode()).hexdigest() + "\n"
    signature = hmac.new(sign_key.encode(), string_to_sign.encode(), hashlib.sha1).hexdigest()
    auth = ("q-sign-algorithm=sha1&q-ak=%s&q-sign-time=%s&q-key-time=%s"
            "&q-header-list=host&q-url-param-list=&q-signature=%s"
            % (secret_id, key_time, key_time, signature))

    with open(local_path, "rb") as f:
        body = f.read()
    req = urllib.request.Request("https://" + host + "/" + cos_key, data=body, method="PUT")
    req.add_header("Authorization", auth)
    req.add_header("x-cos-security-token", token)
    req.add_header("Host", host)
    req.add_header("Content-Type", "text/markdown")
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status
    except urllib.error.HTTPError as e:
        print("HTTP ERROR:", e.code, e.read()[:300])
        return e.code


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print(__doc__)
        sys.exit(1)
    print("HTTP:", cos_put(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]))
