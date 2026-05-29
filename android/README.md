# 一次性：Android 签名 keystore 设置

CI 用 Bubblewrap 把 PWA 打包成 APK，每次推 main 自动重发 GH Release。
你只需要做一次：**生成 keystore + 加 4 个 GH Secret**。装好之后再也不用管。

> ⚠️ keystore 是给 APP 签名的唯一钥匙。生成后**保存到密码管理器/网盘/加密 U 盘**。
> 丢了就再也发不了"升级版"——新建的 keystore 算"不同 APP"，老用户得卸载重装。

## 1) 生成 keystore

### A. 你电脑有 Java（`java -version` 能输出）

```bash
keytool -genkeypair -v \
  -keystore android.keystore \
  -alias android \
  -keyalg RSA -keysize 2048 \
  -validity 36500 \
  -storepass YourSecretPassword \
  -keypass  YourSecretPassword \
  -dname "CN=Oulu Lunch, OU=, O=, L=Oulu, ST=, C=FI"

base64 -w0 android.keystore > android.keystore.b64
cat android.keystore.b64
```

### B. 没有 Java，用 Docker

```bash
docker run --rm -v "$PWD:/work" -w /work eclipse-temurin:17 bash -c '
  keytool -genkeypair -v \
    -keystore android.keystore \
    -alias android \
    -keyalg RSA -keysize 2048 \
    -validity 36500 \
    -storepass YourSecretPassword \
    -keypass  YourSecretPassword \
    -dname "CN=Oulu Lunch, OU=, O=, L=Oulu, ST=, C=FI" && \
  base64 -w0 android.keystore > android.keystore.b64
'
cat android.keystore.b64
```

输出会是一长串 base64 字符串。复制全部。

## 2) 加 GitHub Secrets

开 https://github.com/XiaoyuYuan19/oulu-lunch/settings/secrets/actions

**New repository secret** × 4：

| Name | Value |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | 上一步 base64 字符串 |
| `ANDROID_KEYSTORE_PASSWORD` | 你设的密码 |
| `ANDROID_KEY_ALIAS` | `android` |
| `ANDROID_KEY_PASSWORD` | 同密码 |

## 3) 跑 Action

https://github.com/XiaoyuYuan19/oulu-lunch/actions/workflows/build-apk.yml
→ **Run workflow** → 几分钟后

## 4) 下载 + 装

https://github.com/XiaoyuYuan19/oulu-lunch/releases/latest

下载 `oulu-lunch-1.0.X.apk` → 手机点开 → 允许"未知来源"（设置 → 安全）→ 装。

主屏出现真原生 APP "午饭"，**无 Chrome 角标**，无浏览器框。

## 之后

每次推 main 改前端或 manifest，CI 自动出新 APK 发到 Releases。
直接装新版会无缝覆盖（同 keystore 同包名）。
