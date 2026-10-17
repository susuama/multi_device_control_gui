# BlenderのUIデザイン調査

調査日：2026-10-14

## 1. 目的、参照条件、確認範囲

ユーザーが採用したA案を基準に、Blenderの配色、文字サイズ、文字・部品・内容の余白、角丸、エディター領域の間隔を、根拠付きで参照できるようにする。

参照元はBlender公式リポジトリの**`v4.5.0`タグ、標準のDefaultテーマ**に固定した。前のデモで参照した4.5の公式資料と対応させるための調査上の基準であり、最新版やユーザーの利用中のBlenderを指すものではない。

今回行ったのは公式ソースコードの静的確認である。ユーザーのBlenderのバージョン、保存されたテーマ、UI倍率、OS表示倍率は確認していない。Blender実画面からのピクセル測定も行っていない。

この文書の値は次の三つを区別する。

1. **初期設定値**：テーマやスタイルに記録された値。
2. **基準条件での算出値**：`UI_SCALE_FAC = 1`、`pixelsize = 1`、ビュー内ズームなしを仮定して描画式から得た値。
3. **A案の現状値**：ブラウザー上のCSSピクセル指定。Blender内部の値との直接の同一性は仮定しない。

調査の段階で更新したのはA案採用の記録と本調査文書であり、この時点ではデモのCSSや機能は変更していなかった。その後、ユーザーの依頼で調査結果を反映したA2デモを追加した。ブラウザー向けに選んだ適用値と確認結果は[UIデザインルール.md](<UIデザインルール.md>)を参照する。本書のA案の値は調整前の記録として残す。

## 2. 「パネル」の対応

Blenderでは次の対象を分けて扱っている。

| Blender側の対象 | 例 | 当ソフトウェアでの対応 |
|---|---|---|
| エディターのArea | 3D Viewport、Graph Editor、Image Editorなどを表示する分割領域 | 左上でビューを切り替える共通パネル |
| Region | エディターのヘッダー、メイン表示領域、サイドバーなど | パネル内のヘッダー、操作・表示領域 |
| 設定のPanel | サイドバー等に並ぶ、開閉できる設定項目のまとまり | 機器情報、測定設定などのセクション |
| Widget | ボタン、数値入力欄、チェックボックスなど | 共通の操作部品 |

したがって、設定Panelの余白や角丸を、そのまま共通パネル間の間隔へ適用しない。公式の[領域の説明](https://docs.blender.org/manual/en/4.5/interface/window_system/regions.html)と、下記のArea・Panelの描画コードを合わせて参照する。

## 3. 標準テーマの配色

テーマは部品の種類と状態ごとに色を持つ。背景色一つ、文字色一つで全体を定義しているわけではない。

| 対象・状態 | 確認した初期値 | ソース中の項目 |
|---|---|---|
| 通常ボタンの背景 | `#545454` | `tui.wcol_regular.inner` |
| 通常ボタンの輪郭 | `#3d3d3d` | `tui.wcol_regular.outline` |
| 通常ボタンの文字 | `#e6e6e6` | `tui.wcol_regular.text` |
| 選択された通常ボタン | `#4772b3` | `tui.wcol_regular.inner_sel` |
| 選択状態の文字 | `#ffffff` | `tui.wcol_regular.text_sel` |
| テキスト入力欄の背景 | `#1d1d1d` | `tui.wcol_text.inner` |
| 編集・選択状態のテキスト入力欄背景 | `#181818` | `tui.wcol_text.inner_sel` |
| 数値入力欄の背景 | `#545454` | `tui.wcol_num.inner` |
| 数値入力欄の選択状態 | `#222222` | `tui.wcol_num.inner_sel` |
| エディター間の境界色 | `#161616` | `tui.editor_border` |
| 設定Panelのヘッダー・本体 | `#3d3d3d` | `space_properties.panelcolors.header/back` |
| Propertiesエディター背景のRGB | `#303030` | `space_properties.back`のRGB部分 |
| エディターヘッダーのRGB | `#303030` | `space_properties.header`のRGB部分 |

出典：[標準テーマ定義](https://github.com/blender/blender/blob/v4.5.0/release/datafiles/userdef/userdef_default_theme.c)。この表はテーマ項目の抽出であり、全状態の最終描画色を測定した表ではない。

### 透明度と描画状態

テーマにはRGBAの値もある。例えばProperties背景は`#30303000`、エディターヘッダーは`#303030b3`、通常の輪郭は白の`#ffffff15`、アクティブな輪郭は`#ffffff2a`である。保存されたアルファ値の使い方は描画対象によって異なるため、全項目をそのままCSSの8桁色へ移して同じ見え方になるとは判断しない。

ホバー、無効状態、接合されたボタン、ズームなどにも描画処理がある。必要な状態の最終色は[ウィジェット描画](https://github.com/blender/blender/blob/v4.5.0/source/blender/editors/interface/interface_widgets.cc)と実画面を照合して決める。

## 4. 文字と表示倍率

| 項目 | 確認した初期値 | 根拠 |
|---|---|---|
| 通常の文字サイズ | `11.0` | `UI_DEFAULT_TEXT_POINTS` |
| タイトルの文字サイズ | `11.0` | `UI_DEFAULT_TITLE_POINTS` |
| ツールチップの文字サイズ | `11.0` | `UI_DEFAULT_TOOLTIP_POINTS` |
| タイトル・グループ・操作部品・ツールチップの初期ウェイト | `400` | `ui_style_new`の`character_weight` |
| 標準の可変幅フォント | `Inter.woff2` | `BLF_DEFAULT_PROPORTIONAL_FONT` |
| 標準の等幅フォント | `DejaVuSansMono.woff2` | `BLF_DEFAULT_MONOSPACED_FONT` |

出典：[文字サイズ定数](https://github.com/blender/blender/blob/v4.5.0/source/blender/editors/include/UI_interface_c.hh)、[スタイル初期化](https://github.com/blender/blender/blob/v4.5.0/source/blender/editors/interface/interface_style.cc)、[標準フォント定義](https://github.com/blender/blender/blob/v4.5.0/source/blender/blenfont/BLF_api.hh)。

内部の`points`値は、描画時に`UI_SCALE_FAC`を掛けてフォント描画へ渡される。ズームできる領域では、その領域の倍率も影響する。`11.0`を、そのままCSSの`11pt`や、ユーザー画面の実測11pxと読み替えない。

`UI_SCALE_FAC`はUI設定とモニターのDPI・OS倍率から計算され、モニターによって変わり得る。ウィジェット単位の計算は`round(18 × scale_factor) + 2 × pixelsize`で、基準条件では20になる。

出典：[倍率の定義](https://github.com/blender/blender/blob/v4.5.0/source/blender/makesdna/DNA_theme_types.h)、[DPIとウィジェット単位の計算](https://github.com/blender/blender/blob/v4.5.0/source/blender/windowmanager/intern/wm_window.cc)。

日本語は必要な文字を含むフォントへのフォールバックも考慮する。A案の現在のフォントはSegoe UI／Yu Gothic UI／Meiryo系で、Blenderの標準フォントとは異なる。同じ数値サイズでも文字幅や読みやすさは一致しない。フォントの追加・変更は今回行っていない。

## 5. 文字、部品、内容の余白

### 5.1 スタイルの初期値

以下は`ui_style_new`の値で、利用時にUI倍率を適用する。部品の配置方法によって、間隔が0になる場合や、追加余白が入る場合もある。

| 項目 | 初期値 | 主な意味 |
|---|---:|---|
| `buttonspacex` | 8 | 通常の横方向の部品間隔 |
| `buttonspacey` | 2 | 通常の縦方向の部品間隔 |
| `columnspace` | 8 | 列間隔 |
| `templatespace` | 5 | テンプレート用の間隔設定 |
| `boxspace` | 5 | ボックスの余白設定 |
| `panelspace` | 8 | 設定Panelに使う余白設定 |
| `panelouter` | 4 | 設定Panelの外側に使う余白設定 |

出典：[スタイルの初期値と倍率適用](https://github.com/blender/blender/blob/v4.5.0/source/blender/editors/interface/interface_style.cc)。

[レイアウト処理](https://github.com/blender/blender/blob/v4.5.0/source/blender/editors/interface/interface_layout.cc)では、通常の行に横間隔、列に縦間隔を使う。`align=true`の行・列では、その配置の部品間隔を0にする。関連する部品を密着させたグループと、間隔を置いたグループを使い分けられる。

### 5.2 部品内の文字余白

横方向の文字余白の係数`UI_TEXT_MARGIN_X`は`0.4`。通常の左・右揃えの文字余白を求める処理では、ウィジェット単位と領域の倍率を使う。単位20、ズームなしの条件なら8相当になる。

出典：[文字余白の係数](https://github.com/blender/blender/blob/v4.5.0/source/blender/editors/interface/interface_intern.hh)、[文字余白の使用箇所](https://github.com/blender/blender/blob/v4.5.0/source/blender/editors/interface/interface_widgets.cc)。

数値欄の矢印、アイコン、編集状態などには別の余白処理がある。「全入力欄の全方向のpaddingが8」という規則にはしない。

### 5.3 ヘッダーの高さ

ヘッダーの追加高さ`HEADER_PADDING_Y`は6。通常ヘッダーの高さはウィジェット単位に、倍率を適用した追加高さを足して計算する。基準条件では`20 + 6 = 26`相当。

出典：[ヘッダーの追加高さ](https://github.com/blender/blender/blob/v4.5.0/source/blender/makesdna/DNA_screen_types.h)、[ヘッダー高の計算](https://github.com/blender/blender/blob/v4.5.0/source/blender/editors/screen/area.cc)。6を上下それぞれのpaddingとして重複適用しない。

## 6. 角丸

角丸は対象ごとに別の規則を持つ。

| 対象 | 設定・計算 | 基準条件での値 |
|---|---|---|
| 通常ボタン等 | テーマの`roundness = 0.2` × ウィジェット単位 × ズーム | 単位20なら4相当 |
| 設定Panel | テーマの`panel_roundness = 0.4` × 単位 × 0.5 ÷ 領域のaspect | 単位20、aspect=1なら4相当 |
| エディターのArea | `EDITORRADIUS = 6 × UI_SCALE_FAC` | 6相当 |

出典：[角丸のテーマ初期値](https://github.com/blender/blender/blob/v4.5.0/release/datafiles/userdef/userdef_default_theme.c)、[ウィジェットの角丸計算](https://github.com/blender/blender/blob/v4.5.0/source/blender/editors/interface/interface_widgets.cc)、[設定Panelの角丸計算](https://github.com/blender/blender/blob/v4.5.0/source/blender/editors/interface/interface_panel.cc)、[Areaの角丸定数](https://github.com/blender/blender/blob/v4.5.0/source/blender/editors/screen/screen_intern.hh)。

一部の部品では高さから半径を計算する。隣接して接合された部品の角の処理もある。全要素へ一律に同じCSSの`border-radius`を指定することとは異なる。

## 7. エディター領域の間隔と境界

初期設定では`border_width = 2`。これはAreaの周囲の間隔を管理する値で、設定Panelの`panelspace = 8`とは別である。

Areaの範囲計算では、内部の境界に接する辺をこの値とUI倍率から求めた分だけ内側へ縮める。画面の外周や単独・最大化状態には分岐がある。境界の描画には幅、角丸、輪郭が加わる。

したがって、`border_width = 2`だけから、完成した画面のパネル間が一律にCSSの`gap: 2px`と同じだとは判断しない。整数座標、両側の領域、境界描画を含む見た目は実画面との照合が必要である。

操作判定は見た目より広く、`BORDERPADDING`では`border_width × scale + 3 × scale`を使う。基準条件なら5相当の判定幅設定になる。見た目の細い境界と、ドラッグしやすい操作領域を分ける参考になる。

出典：[境界幅の初期値](https://github.com/blender/blender/blob/v4.5.0/release/datafiles/userdef/userdef_default.c)、[Area範囲の計算](https://github.com/blender/blender/blob/v4.5.0/source/blender/editors/screen/area.cc)、[境界の描画](https://github.com/blender/blender/blob/v4.5.0/source/blender/editors/screen/screen_draw.cc)、[境界の操作判定](https://github.com/blender/blender/blob/v4.5.0/source/blender/editors/screen/screen_intern.hh)。

## 8. A案との比較と、次の調整案

| 項目 | A案の現状 | Blenderで確認した参照値 |
|---|---|---|
| 通常の部品背景 | `#484848` | 通常ボタン`#545454` |
| テキスト入力欄 | `#282828` | `#1d1d1d` |
| 基本文字色 | `#dddddd` | 通常ボタン文字`#e6e6e6` |
| 選択・主要操作色 | `#4772b3` | 通常の選択色も`#4772b3` |
| 基本文字サイズ | CSS 12px | 初期値11.0、描画倍率を適用 |
| ボタン高 | CSS 22px | ウィジェット単位は基準条件で20 |
| 共通パネルヘッダー | CSS 28px | 通常ヘッダーは基準条件で26 |
| ボタンの角丸 | CSS 3px | 通常部品は基準条件で4相当 |
| 共通パネルの角丸 | CSS 3px | 対応するAreaは基準条件で6相当 |
| パネル間 | 6pxの境界領域＋各パネルの枠 | `border_width=2`とArea・境界描画の組み合わせ |

次の変更候補は、A案の情報密度を保ったまま、通常部品の背景・文字・入力欄の色をテーマの対応値へ寄せること。その後、文字サイズ、部品高、余白、角丸、境界を別々に比較する。

設定項目では、同じまとまりの縦間隔を小さくし、まとまり同士の区切りに余白を使う方針を検討できる。境界は見た目の幅と操作可能な幅を分ける。

これらは調査時点の**調整案**だった。その後のA2デモでは、配色、CSS文字11px、操作部品20px、ヘッダー26px、通常部品4px・共通パネル6pxの角丸、設定項目の縦2px・横8pxを取り入れた。パネル境界は見た目4px・操作範囲10pxとしてブラウザー向けに選んだ。A2の製品デザインとしての採用は未確定で、調整前のA案を比較用に残している。

## 9. 実画面で追加確認する項目

- 比較対象のBlenderバージョン、テーマ、UI倍率、OS倍率をそろえる。
- ボタン、数値入力欄、テキスト入力欄、エディターヘッダー、設定Panel、領域境界を別々に確認する。
- ホバー、選択、編集、無効状態も確認する。
- 日本語ラベル、長い機器名、桁数の多い数値と単位を表示して読みやすさを確かめる。
- 間隔は、内容間、部品間、部品内部、領域間、操作判定の幅を分けて測定する。

ソースコードの値と画面の測定値を区別して記録し、最後に当ソフトウェアで再現するCSS値を定める。
