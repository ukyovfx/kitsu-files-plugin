# M2 — 個人Google連携と共有Drive権限の設計（v2, 2026-10-22）

状態: **設計・検証計画のみ。実装なし。実Google API呼び出しなし。** 根拠は `a498a5b` のコード、リポジトリ内の既存セキュリティ文書、Google公式ドキュメント。
公式文書で確認できなかった点は「要実機検証」と明記する。v1からの主な変更は §1（閲覧モデル）と §3（OAuth分離）。

## 0. 確定仕様と現行実装の矛盾

確定仕様: **共有Driveの許可メンバーだけがファイルを閲覧できる。**
現行のダウンロードは `/output-package-files/<id>/download` で、Kitsuの Task read 権限だけを判定し、実バイトは **Production認証情報で取得して返す**（`resources.py` `OutputPackageFileDownload`）。
つまり Googleグループ／Drive ACL から外された人でも、Kitsuメンバーである限りPlugin経由で内容を取得できる。**仕様と矛盾する。** 修正が必要。

その他の事実（コード確認済み）:
- `DriveCredential` は `connection_id` 主キー（Production接続ごとに1つ）。個人単位の認証情報は無い。
- Pluginは共有リンクを作らず（`permissions`/`anyone`/`webViewLink` を一切扱わない）、Drive ACLを読みもしない。
- Output取り込みはProduction認証情報で原本を読み、`output-package-<id>` へコピー。原本は allowed root 配下に限られる。

## 1. 閲覧モデル（優先: Google認証 + Drive ACL のリンク）

### 方針
通常の閲覧・ダウンロードは **Driveのリンク（`https://drive.google.com/file/d/<id>/view`、フォルダは `/drive/folders/<id>`）を開く**方式を既定とする。アクセス可否は **Google認証と共有DriveのACLが決める**。Pluginはバイトを中継しない。

| | A. Driveリンク（既定・推奨） | B. Plugin経由ダウンロード（制限付きで残す） |
|---|---|---|
| 認可の主体 | Google（共有Driveのメンバー／Googleグループ／ロール） | Kitsu Task read（現行） |
| 仕様適合 | ◎ 許可メンバーのみ | ✗ 現行のままでは不適合 |
| 実装 | リンク生成のみ（ID既知）。APIコール不要 | 既存 |
| 残す条件 | — | FakeStorage UAT、Production管理者のブレークグラス、管理者設定で明示有効化した場合のみ |

Bに追加する制限（必要な場合のみ）: ①Production設定 `proxy_download`（`off`既定 / `admins_only` / `task_members`）を新設、②`off` では 403（FakeStorageモードは従来どおり許可）、③`task_members` を選ぶ場合は「Driveの閲覧権限とは独立であり、Googleグループ除外を反映しない」旨を管理画面に明示。
**PluginはKitsuユーザーのDrive権限を検証できない**（`drive.file` の本人トークンは未選択ファイルを見られず、Directory API は管理者スコープが必要）。よって「Googleのメンバーか」を条件にプロキシを許可する設計は成立しない。これが B を既定OFFにする理由。

### 画面ごとの扱い（維持するもの）
- **Task詳細 / 版履歴**: 各ファイル行・版行に「Driveで開く」リンク（共有Drive内のIDから生成）。バージョン番号・登録者・QC・送信履歴の表示は不変。
- **Send / Receive**: 受領側も同じリンク。アクセス不可なら Google が拒否する（Plugin内は「権限がない場合はDrive管理者へ」案内文のみ）。
- **削除済み（PR #19）**: リンク非表示・「削除済み」表示。リンクが残っていても実体は削除済みで 404。
- **削除機能**: Plugin上の権限（登録者/管理者）は不変。Driveのロールとは別軸（§4）。

### 共有Drive側の調査結果
- **ロール**（Google管理ヘルプ）: Manager / Content manager / Contributor / Commenter / Viewer。Viewer は閲覧のみ。Contributor は削除不可。Content manager はゴミ箱へ移動のみ。**完全削除・メンバー管理は Manager のみ**。
- **Googleグループ**: メンバー追加に「名前・メール・Googleグループ」を指定できる。→ 管理者がグループでメンバーを管理する運用は成立する（KitsuとGoogleグループの自動同期は今回スコープ外）。グループにはロール（Viewer等）を付与する。
- **Viewerの可能操作**: 管理者設定「Viewer/Commenterによるダウンロード・印刷・コピーを許可」を外すと不可（API上は `restrictions.copyRequiresWriterPermission` / `downloadRestriction`）。つまり「閲覧のみ」か「ダウンロード可」かは**Drive側設定**で決まり、Pluginは制御しない。
- **`driveMembersOnly`**（API `drives.restrictions.driveMembersOnly`）: ドライブ内アイテムへのアクセスをメンバーに限定。管理コンソール上の対応は「共有ドライブのメンバー以外をファイルに追加することを許可」をOFFにする設定に近いが、**名称は同一ではなく、対応関係は要確認**。これと「ドライブ外のユーザーのアクセスを許可」OFFの併用が「リンクを知っている人全員」を実質的に封じる。設定はManagerまたは管理者のみ（OU単位の既定＋ドライブ単位）。Business Starter では既定設定が使えず全て許可側。
- **外部Googleアカウント**: 組織が許可していれば、Googleアカウントに紐づくメールアドレスの外部ユーザーを追加できる（外部が作成したコンテンツの所有権は組織に移る）。管理者が「組織外ユーザーによる共有ドライブへのアクセス」をOFFにすると追加自体が不可。Workspace版に共有ドライブが含まれない相手は Viewer のみ。**外部Googleグループ／個人Gmailをグループ経由で入れられるかは要実機検証。**
- **マイドライブ内の共有フォルダとは別構成**: そちらは共有ドライブの `restrictions`/メンバー概念が無く、ファイル/フォルダ個別の `permissions` 継承で決まる。`allowed_root` 検証経路（OIDC `sub`）も異なる。本設計の保証（driveMembersOnly等）は**共有ドライブ限定**で、マイドライブ構成には適用しない。

### Pluginが検証できる範囲 / できない範囲
| 検証内容 | 可否 | 手段 |
|---|---|---|
| Pluginが作ったファイルに `anyone`/`domain` 権限が無い | 可（要実機確認） | `permissions.list`（`drive.file`でアプリ作成物には可の見込み） |
| 共有Driveが `driveMembersOnly` 等で制限されている | 不明 | `drives.get` が `drive.file` で読めるか未確認。読めなければ管理者チェックリスト運用 |
| 特定Kitsuユーザーがドライブのメンバーか | **不可** | 本人トークンは未選択ファイルを見られない。Directory APIは管理者スコープ |
| リンクの先で誰が開けるか | Google側で強制 | Pluginは保証しない（接続時の警告表示までに留める） |

## 2. 乖離（Kitsu権限とDrive ACL）の扱い
リンク方式では Drive ACL が閲覧の唯一の権威になる。Kitsuメンバーから外した人がDriveには残る／Driveから外した人がKitsuには残る、は**運用（同期は今回実装しない）**で管理。Pluginは Task read 権限者にのみリンクを表示する（リンク漏洩面の縮小。権威ではない）。

## 3. OAuthの分離設計

| | Production OAuth（既存） | 個人OAuth（新規・任意） | Picker用の短命トークン |
|---|---|---|---|
| 主体 | Production管理者が認可した1アカウント | 各Kitsuメンバー本人 | 本人（ブラウザ内） |
| 保管 | **サーバーのみ**（暗号化refresh token） | **サーバーのみ**（暗号化refresh token、`actor_id` 単位） | **サーバーに保管しない**。ブラウザのメモリのみ |
| 用途 | 共有Driveへの作成・読取・削除、プロキシ | 本人が選択した原本の**読み取り** | Pickerの表示と選択のみ |
| ブラウザへ返す | **禁止（絶対）** | 禁止（statusのみ） | 本人のアクセストークンのみ（1時間級・refreshなし） |
| scope | `openid drive.file` | `openid drive.file` | `drive.file` のみ |
| 共有Driveへの書込 | ○ | **×（コードで構造的に不可）** | × |

ポイント:
1. **Productionトークンはブラウザへ返さない。** Production側のブラウズ（`TaskStorageObjects`）は現行どおりサーバーAPI経由。Pickerは個人Drive専用。
2. **Picker用トークンは Google Identity Services のトークンクライアント（ブラウザ内、refresh tokenなし）で本人が取得**し、サーバーは発行も保存もしない。サーバーがrefresh tokenから作ったアクセストークンをブラウザへ渡す経路は作らない（個人refresh tokenの権限をブラウザに広げないため）。
3. サーバーは Pickerが返した `fileId` を**信頼しない入力**として扱い、個人refresh tokenで `files.get` を再検証（存在・サイズ・MIME・非ゴミ箱）してから取り込む。選択されたファイル群はManifestに固定。
4. `drive.file` は**書き込み能力を含む**（アプリがアクセスできるファイルに対し、本人が書込権限を持てばAPI上は変更・削除が可能）。個人トークンで原本を壊せるため、最小権限を**コードで強制**する:
   - 個人grantで使うアダプタを **読み取り専用クラス**（`get` / `download_to` / `list_children` のみ公開、`upload`/`delete`/`create_folder` を持たない）に限定。
   - テスト: 個人アダプタ経由のHTTPメソッドが `GET` のみであることを検証（Driveエミュレータ）。
   - CI/レビュー規約: 個人grantを受け取る関数から Productionアダプタの書込メソッドを呼ばない。
   - 代替案: `drive.readonly`（制限スコープ。審査・セキュリティ評価が必要）なら**プロトコル上も読み取り専用**になるが、コスト大。当面は `drive.file` + コード強制、将来の判断事項。
5. Googleの `drive.file` 付与は「ユーザー × アプリ(OAuthクライアント/Cloudプロジェクト)」単位の見込みで、**Picker用とサーバー用で同じOAuthクライアントIDを使う必要**がある（別クライアントだとPickerで渡したファイルがサーバートークンから見えない可能性）。要実機検証。
6. 共有Driveへのコピーは常に Production認証情報が実行（個人トークンで作成したファイルはProduction認証情報から見えない/削除できないため）。取り込みは現行どおり Worker経由ストリーミング（個人トークンで読む→Productionで書く）。
7. **アカウント連携の許可/禁止**はProduction単位の設定（`ProductionOutputSetting` と同様の1設定）。禁止中は個人OAuth開始・Picker・取り込みAPIをサーバー側で拒否。既存の取り込み済みOutputは影響を受けない。
8. 失効: 個人grantの解除はGoogleへrevoke＋暗号化領域の消去（既存の revoke Job と同型）。Production離脱/ロール変更は**取り込みAPIの都度Kitsu権限を再判定**して拒否。

## 4. 権限表（更新）

| 操作 | 一般メンバー | Output登録者 | Production管理者 | Google側で必要な条件 |
|---|---|---|---|---|
| Outputを閲覧/ダウンロード（既定） | リンクを開く | 同左 | 同左 | **共有Driveのメンバー（Viewer以上、グループ可）** |
| Plugin経由ダウンロード | 設定`proxy_download`に従う（既定: 不可） | 同左 | 可（ブレークグラス） | Production認証情報の読取 |
| 個人Google連携/解除 | ○（許可設定時） | ○ | ○ | 本人の `drive.file` 同意 |
| 個人Driveの原本を選択・取り込み | Taskの`publish`権限者 | ○ | ○ | 読取=個人トークン、書込=Production認証情報（共有ドライブContributor以上） |
| Outputの完全削除 | 不可 | 設定が「登録者＋管理者」のとき自分の分 | ○ | Production認証情報が共有Driveの **Manager（Organizer）** |
| 削除ポリシー / 個人連携許可 / `proxy_download` の変更 | 不可 | 不可 | ○ | なし（Kitsu判定） |
| 共有Drive・Googleグループ・`restrictions`の管理 | 不可 | 不可 | Google管理者/Manager（Plugin外） | Google側のみ |

Driveの閲覧権限とPlugin上の変更・削除権限は別軸（Plugin操作の可否はKitsuと登録者ID、Driveロールは Production認証情報が実行できるかの前提条件）。

## 5. 個人OAuth試作の差分監査（読み取り専用・未変更）

対象: `worktrees/per-user-drive-oauth-prototype-20261019`（HEAD `a498a5b`、未コミット38ファイル。一切変更していない）。

### 構成
- **個人OAuth**: `integrations/user_oauth.py`(487行)、`user_drive_access.py`(128行)、migration `0022_user_drive_oauth`、`DriveAccountControl.vue`、テスト2本。
- **無関係な大規模変更が同居**: migration `0021_output_first_handoffs`（Output-first承認/Handoff、`output_version` 採番テーブル等）、`resources.py` +388行（承認/Handoff/個人OAuth route）、`handoffs/service.py`、`packages/service.py`・`worker.py`、`folders/*`、`approvals/*`、`models.py` +96行。

### migration衝突
| 項目 | PR #19 | 試作 |
|---|---|---|
| `vxf_0021` | `0021_output_version_deletion`（削除記録・設定） | `0021_output_first_handoffs`（承認/Handoff） |
| `vxf_0022` | なし | `0022_user_drive_oauth`（`down_revision = vxf_0021`） |
| `schema_epoch` | 20 | 0021→20、0022→21 |
| `compat.py` | SCHEMA_EPOCH/WORKER_BUILD=20 | 同ファイルを別値で変更 |

同じリビジョンIDと同じepochを両方が主張しており、**そのままでは同一系統に載せられない**。どちらかが先に入る場合、後から入る側を `vxf_0022`/`0023` へ付け替え、`down_revision`・epoch・`compat.py`・スキーマテストを再整理する必要がある。PR #19 は未マージのため、どちらを先にするかは人間の判断事項。

### 再利用 / 破棄
| 部分 | 判断 | 理由 |
|---|---|---|
| `user_oauth.py` の骨格（単回使用intent、PKCE、固定callback、暗号化grant、`google_subject`一意、scope厳密一致、解除=revoke+消去、statusのみ返却） | **再利用候補（要レビュー）** | 既存 `CredentialKeyring`/`oauth.py` を再利用しており、トークンをブラウザへ返さない。個人grantは `actor_id` 単位でProductionに非依存 |
| `0022_user_drive_oauth` のテーブル設計 | **再利用候補** | 2テーブルのみ、downgrade保護あり。リビジョン番号の付け替えが前提 |
| 個人OAuth route `/productions/<id>/drive/me` | **要改修** | 管理者の「個人連携 許可/禁止」判定が無い（要追加）。個人grantは Production非依存なのでrouteの粒度も再検討 |
| `user_drive_access.py` `verify_picked_object` | **破棄/再設計** | 個人トークンで**Production root配下**（`assert_parent_within_root`）の選択物を検証する設計。確定仕様の原本は**個人Drive（root外）**で、`drive.file` の個人トークンはProduction rootを `capabilities()` で検証できない可能性が高い。取り込み元の前提が逆 |
| アダプタが読み取り専用でない点 | **要修正** | 個人トークンのアダプタが `upload`/`delete` を持ちうる。§3-4 の読み取り専用クラスに置換 |
| `0021_output_first_handoffs` と承認/Handoff一式 | **本件と無関係・別PR** | M2の個人OAuth設計と独立。取り込まない。PR #19（削除）との相互影響（Handoffと版の固定）は別途整理 |
| `packages/worker.py`(+13) | **要確認** | MIME修正（`codex/fix-package-folder-mime`）と重複・衝突しうる |
| `DriveAccountControl.vue` ほかUI | **後回し** | 設計確定後に最小UIとして再作成 |
| `plugin/frontend/dist` 差分 | **破棄** | ビルド生成物。統合時に再生成 |

結論: **ブランチ丸ごとの採用は不可**。`user_oauth.py` と `0022` の骨格だけを、承認/Handoffの変更と切り離した新しいブランチで再レビューして取り込むのが最小経路。試作は上書き・削除しない。

## 6. 実機検証計画（未実施・要明示承認）

**前提**: 専用の検証用Google Workspaceテナント（本番と無関係）、共有Drive1つ（検証用）、アカウント: 管理者A、メンバーB、非メンバーC、外部Gmail D。OAuthは**検証用クライアントID**。本番Productionと既存共有ファイルは対象外。**Googleアカウント・Driveを使う操作は、この環境の用意と人間の明示承認を得るまで実行しない。**

| # | 検証 | 手順 | 成功条件 |
|---|---|---|---|
| V1 | 共有Driveのアクセス拒否 | AのProduction認証情報で取り込み→生成ファイルのリンクをB(Viewerグループ)・C(非メンバー)・D(外部)で開く | Bは閲覧可、Cは拒否（アクセス権リクエスト画面）、Dは設定に従い拒否/可。Plugin経由でなく**Google画面で判定される** |
| V2 | グループ運用 | Bをグループから除外 | Bが以後リンクを開けない。PluginのTask表示は変わらないこと（乖離の確認） |
| V3 | `driveMembersOnly`相当 | 管理者設定で非メンバー共有OFF→メンバーが非メンバーへ共有を試す | 共有が拒否される。`permissions.list` でアプリ作成物に `anyone` が無い |
| V4 | `drives.get` の可否 | `drive.file` トークンで `drives.get` / `permissions.list` | 読めるか/403か（読めなければ管理者チェックリスト運用に確定） |
| V5 | Viewerのダウンロード | 管理者設定を切替 | 「Viewerのダウンロード許可」OFFで Viewer がDL不可になること |
| V6 | Picker単体ファイル | GISトークンクライアント＋Pickerで個人Driveのファイルを1つ選択 | サーバー（同一クライアントIDのrefresh token）から選択ファイルのみ `files.get`/download可。未選択は404 |
| V7 | Pickerフォルダ/EXR連番 | フォルダ選択、複数選択（数百フレーム） | 子ファイルが見えるか、選択件数上限。見えない場合は連番方式を再決定（§7-2） |
| V8 | 別クライアントID | Pickerとサーバーで別client IDを使用 | 選択ファイルがサーバーから見えない（＝同一ID必須の確認） |
| V9 | 個人トークンの書込防止 | 個人アダプタ経由で変更系メソッドを試みるコードパスがないこと＋エミュレータでGETのみ | 変更系リクエストが0件（自動テスト） |
| V10 | 共有Driveへのコピー | 個人→共有Driveへ Worker経由コピー | 共有Drive内ファイルの所有はドライブ、Production認証情報で読取/削除可。原本は無変更・SHA一致 |
| V11 | 削除権限 | ProductionのアカウントをContent managerに降格 | 完全削除が `provider_permission_denied` でfail closed、記録が`deleted`にならない。Managerで成功 |
| V12 | 失効 | 個人grantを本人がGoogle側で取消／Plugin側で解除 | 以後の取り込みは拒否、取り込み済みOutputは閲覧・削除に影響なし |
| V13 | 外部Gmail | Dを共有Driveに招待（許可設定時）／個人GmailでPicker利用 | 招待可否・Picker可否・OAuth同意の挙動を記録 |
| V14 | 実Driveでの取り込み（MIME） | `codex/fix-package-folder-mime` 適用後に実Driveでパッケージ取り込み | 版フォルダが確認され`completed`になる（`application/vnd.google-apps.folder`） |
| V15 | マイドライブ構成 | 共有ドライブとは別に、マイドライブ内共有フォルダで同一シナリオ | 結果を別表に記録（共有ドライブと同一視しない） |

各検証は、結果（HTTPステータス・Googleの拒否画面・`permissions` の有無）のみを記録し、トークン・認可コード・実ファイル内容は記録しない。

## 7. 未解決事項と人間の決定事項
1. 個人原本の取り込み方式: Picker（短命トークンのブラウザ保持を許容）／allowed rootへ手動アップロード／`drive.readonly`（審査要）。
2. EXR連番・フォルダを必須要件にするか（Pickerでフォルダの子へアクセスが及ぶか未確認 = V7）。及ばない場合は「個別選択の限界」か「原本のアップロード経路」。
3. Plugin経由ダウンロード `proxy_download` の既定（推奨: off）と、ブレークグラスの範囲。
4. Production認証情報を誰のアカウントにするか（共有Driveの Manager 必須、人的依存の回避＝共有/サービス用アカウント）。
5. 外部Gmail・外部グループの許可方針（V13の結果次第）。
6. 試作の取り扱い: `user_oauth.py`+`0022` のみ再レビューして取り込む案（§5）を採るか。migration番号の優先（PR #19 を先に、が最小）。
7. 個人grant失効時に保留中の取り込みをどう扱うか。
8. 他の `"folder"` リテラル比較（タスクフォルダ生成・テンプレート探索・`resources.py` 一部）が実Driveで同様に失敗しうる。取り込みMIME修正とは別に調査・修正が必要（実Drive統合試験で確認）。

## 8. 最小実装マイルストーン（更新）
- **M2-0**: §6 の実機検証（V1〜V15）。結果で §7 の1・2・3・5を確定。
- **M2-1**: リンク表示への切替（`proxy_download` 設定追加、UIに「Driveで開く」）。個人OAuth無しでも仕様適合に近づく最小変更。
- **M2-2**: 個人OAuth（試作の骨格のみ再利用、許可/禁止設定、読み取り専用アダプタ）。
- **M2-3**: Pickerで単一/複数ファイル取り込み（Worker経由コピー）。
- **M2-4**: 連番/フォルダ（V7の結果次第）。
- **M2-5**: 共有設定検査（`permissions.list`、可能なら`restrictions`）と管理者向け警告。
