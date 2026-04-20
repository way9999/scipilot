# SciPilot Release

## 1. 生成自动更新签名

首次执行时在项目根目录生成 updater key：

```powershell
pnpm tauri signer generate -- -w .tauri/updater.key --ci
```

公钥已经写入 `src-tauri/tauri.conf.json`。私钥必须妥善保存，后续所有更新都必须使用同一把私钥签名。

## 2. 本地打包

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1
```

脚本会：

- 设置 `TAURI_SIGNING_PRIVATE_KEY=.tauri/updater.key`
- 执行 `pnpm tauri build`
- 生成 GitHub Release 可用的 `latest.json`
- 将安装包、签名文件和 `latest.json` 复制到 `release/<version>/`

如果安装包已经打好，只想重建 `latest.json` 或重新整理发布目录：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -SkipBuild
```

## 3. 上传到 GitHub Release

确保 `gh auth status` 正常，然后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\publish_release.ps1 -Repo way9999/scipilot
```

注意：
- `way9999/scipilot` 必须存在，且当前 GitHub 账号有权限访问。
- 如果仓库名写错，脚本现在会直接失败，不会再误报“Uploaded release assets”。

默认行为：

- 使用 `package.json` 的版本号创建 `v<version>` tag release
- 上传 `release/<version>/` 下的全部制品
- 覆盖同名旧资产

## 4. 客户端更新地址

客户端 updater 当前读取：

```text
https://github.com/way9999/scipilot/releases/latest/download/latest.json
```

只要最新 release 中带有 `latest.json` 和对应平台安装包，软件内“检查更新/立即更新”即可直接拉取最新版。
