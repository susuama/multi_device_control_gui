# 依存ライブラリとライセンス

自作のコード・文書・デモは [MIT License](LICENSE)。第三者のライブラリ・付属素材には、それぞれのライセンスが適用される。

## 1. 依存ライブラリ

現在使用している主なライブラリは以下の通り。

| ライブラリ | ライセンス | 用途 |
|---|---|---|
| FastAPI / Pydantic | MIT | API・データ型 |
| Uvicorn / Starlette | BSD-3-Clause | APIサーバー・ASGI |
| pywebview | BSD-3-Clause | Windowsの画面表示 |
| Rich / AnyIO | MIT | ログ表示・非同期処理 |
| HTTPX | BSD-3-Clause | APIテスト |
| React / React DOM / Recharts | MIT | フロントエンド |
| Vite / TypeScript | MIT / Apache-2.0 | 開発・ビルド |

Pythonの導入対象は `requirements.txt` と `requirements-dev.txt`、フロントエンドのバージョンとライセンス情報は `frontend/package-lock.json` に記録。間接依存にもMPL-2.0、CC-BY-4.0などのライセンスが含まれる。
ライブラリを同梱して配布する場合は、その配布物に含まれるライセンス・著作権表示も保持する。

## 2. 初期テンプレートと付属素材

フロントエンドは [create-vite](https://www.npmjs.com/package/create-vite) のReact / TypeScriptテンプレートを使用。
以下の素材は、公開準備時に公式配布物 `create-vite 9.2.1` の対応ファイルとバイト単位で一致することを確認。プロジェクト作成時のcreate-viteのバージョンは未記録。

- `frontend/public/favicon.svg`
- `frontend/public/icons.svg`
- `frontend/src/assets/hero.png`
- `frontend/src/assets/react.svg`
- `frontend/src/assets/vite.svg`

これらはテンプレートから引き継いだ素材として残す。テンプレートの [ViteのMITライセンス](https://github.com/vitejs/vite/blob/main/LICENSE) と著作権表示を以下に保持する。Reactの著作権表示もあわせて記載。
ロゴ・サービス名の商標に関する権利は各権利者に帰属する。

### Vite / create-vite

```text
MIT License

Copyright (c) 2019-present, VoidZero Inc. and Vite contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### React

```text
MIT License

Copyright (c) Meta Platforms, Inc. and affiliates.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 3. UIデモと過去のデザイン案

グラフの数値とビーム画像は `DesignDemo.tsx` 内の数式で生成した見本データ。画面用アイコンは同ファイル内のSVG定義を使用。
フォントはWindowsのシステムフォントを参照し、フォントファイルは同梱していない。

Blenderを参考にした配色・寸法の出典は [BlenderのUIデザイン調査](<design-demo/archive/2026-10-15/Blender UIデザイン調査.md>) に記載。Blender本体のコード、テーマファイル、アイコン、フォントは同梱していない。
`design-demo/archive` と `_trash` の内容は、過去の比較・試行の記録として保持する。

## 4. 実機対応で使用を想定しているソフトウェア

| ソフトウェア | ライセンス・利用条件 | 資料 |
|---|---|---|
| PyLabLib | [GPLv3](https://github.com/AlexShkarin/pyLabLib/blob/main/LICENSE) | [公式ドキュメント](https://pylablib.readthedocs.io/en/stable/) |
| PyVISA | [MIT](https://github.com/pyvisa/pyvisa/blob/main/LICENSE) | [公式ドキュメント](https://pyvisa.readthedocs.io/en/stable/) |
| ThorCam / uc480 SDK / Optical Power Monitor | Thorlabsの利用条件 | [ThorCam](https://www.thorlabs.com/software-pages/ThorCam) / [OPM](https://www.thorlabs.com/software-pages/OPM) |
| NI-VISA Runtime | NIの利用条件 | [NI-VISA](https://www.ni.com/ja/support/downloads/drivers/download.ni-visa.html) |

現在のアプリの依存関係にはPyLabLibを含めていない。`doc/ThorCam.md` と `doc/OPM.md` は、実機用環境を別途準備するための資料。

PyLabLibを組み込んだプログラムを配布する場合は、結合したプログラム全体についてGPLv3の条件を満たす必要がある。自作部分のMITの指定によって、PyLabLibの条件が免除されるわけではない。
メーカー製SDK、DLL、ドライバー、インストーラーは同梱せず、各公式サイトから取得する。
