# M2 — 個人Googleアカウント連携と共有Drive権限の設計監査（2026-10-22）

状態: **設計・検証計画のみ。実装なし。** 根拠は `a498a5b` のコード、リポジトリ内の既存セキュリティ文書、Google公式ドキュメント。
Google側の挙動で公式文書から確認できなかった点は「要実機検証」と明記する。実Google APIは一切呼んでいない。

## 1. 現行実装の事実（コードで確認済み）

| 項目 | 現状 |
|---|---|
| 認証情報の単位 | `DriveCredential` は **`connection_id` 主キー（Production接続ごとに1つ）**。暗号化された `openid drive.file` の grant。個人ユーザー単位の認証情報は無い。 |
| 認証情報の所有者 | Production管理者（`Capability.PROVISION` = Zou `admin`/`manager`）が OAuth を開始。grantの主体は「その時認可したGoogleアカウント」であり、以後の全Drive操作（フォルダ作成・アップロード・ダウンロード・削除）はこの1アカウントで行う。 |
| 保存先 | `ProductionDriveAuthority` / `StorageConnection.allowed_root_id`。My Drive は OIDC `sub` で所有者を束縛、Shared Drive は Drive ID で束縛。 |
| Outputの取り込み | `packages/worker.py`: Production認証情報で原本を `download_to` し、`output-package-<id>` フォルダへ `upload`（バイトはWorker経由、Zou/ブラウザは通らない）。原本は **allowed root配下** に限定（`assert_parent_within_root`）。**個人Driveの原本は現状読めない。** |
| ダウンロード | Plugin経由（`/output-package-files/<id>/download`）。Kitsuの Task read 権限で判定し、実バイトは Production認証情報で取得。**Driveの閲覧ACLはこの経路では効かない**（Kitsu権限が単独で効く）。 |
| 共有・リンク | Pluginは `permissions` / `anyone` / `webViewLink` を **一切扱わない**（grep確認）。共有リンクを作らず、共有設定を読みもしない。 |
| Picker | 未実装。`google-drive-existing-root-access.md` が「web OAuthでの one-pick は未確認、実装しない」と決定済み。 |
| 未コミット試作 | `worktrees/per-user-drive-oauth-prototype-20261019`（38ファイル未コミット）に `user_oauth.py` / `user_drive_access.py` / migration `0022`。**採用・レビュー済みではない。** その `0021_output_first_handoffs` は PR #19 の `vxf_0021` と**リビジョンIDが衝突**する（統合時に要整理）。 |

## 2. 重要な設計上の事実（Google側）

1. `drive.file` は非機微スコープ（基本のOAuth検証のみ）。アクセスできるのは「アプリが作成した」「ユーザーがPickerで渡した/アプリで開いた」ファイル。**権限は「ユーザー × アプリ」単位**であり、別ユーザーのトークンで作ったファイルは、そのユーザー以外のトークンからは（Pickerで渡されない限り）見えない。→ 共有Driveへのコピーを個人トークンで作ると、Production認証情報から削除・ダウンロードできなくなる。**共有Driveへの書き込みは常にProduction認証情報で行うべき。**
2. `files.delete` は `drive.file` で許可されるが、**共有Driveでは親フォルダの Organizer（Manager）権限が必須**。フォルダ削除は所有する子孫を全て削除する（PR #19 M1でフォルダ自体を削除しない設計にした根拠）。
3. 共有Driveのロール: Manager / Content manager / Contributor / Commenter / Viewer。Contributor は削除不可、Content manager はゴミ箱へ、**ゴミ箱の完全削除と構成員管理は Manager のみ**。新規メンバーの既定は Content manager。メンバー追加には **Googleグループ** を指定できる（Google管理ヘルプ）。
4. 共有Driveの `restrictions`（`driveMembersOnly`, `domainUsersOnly`, `copyRequiresWriterPermission`, `sharingFoldersRequiresOrganizerPermission`）は `drives.update` で変更（Managerまたは管理者）。これが「リンクを知っている人全員」を抑止する実効的な強制点。
5. Picker（web）は **OAuthアクセストークンをブラウザに渡す**（`setOAuthToken`）。「トークンをブラウザへ返さない」という現行方針（`google-oauth-credential-operations.md`）と衝突する。desktop/mobile 向け one-pick は `drive.file` 単独のみ許可で `openid` と併用不可、web対応は未確認。
6. 個人Google（非Workspace）アカウントでも OAuth（`drive.file`）とPickerは利用可能。ただし **共有Driveの作成は Workspace 限定**で、個人アカウントを共有Driveのメンバーにできるか／外部グループを使えるかは組織の共有設定次第（**要実機検証**）。Googleグループによるメンバー管理は Workspace 管理者の領域。

## 3. 推奨アーキテクチャ（最小・既存再利用）

**「認証情報の所有者」と「実際のアクセス権」を分離する二層モデル。新サービス・権限同期エンジンは作らない。**

- **層A: Production保存先認証情報（既存のまま）**。管理者が認可した1アカウント（共有Drive の Manager 推奨）。共有Driveへの作成・読み取り・削除は**全てこの認証情報**。Plugin上の「誰が何をできるか」は Kitsu/Zou のロール・割当・登録者IDで判定（PR #19 と同じ）。
- **層B: 個人Google連携（新規・任意・読み取り専用）**。Productionメンバーが自分のGoogleアカウントを連携し、**自分がPickerで選んだ原本だけ**を読む。個人トークンは「原本のダウンロード」にのみ使い、**共有Driveへの書き込みには使わない**。取り込みは現行の Worker ストリーミング（個人トークンで読む → Production認証情報で `upload`）を拡張するだけで、個人Driveの原本は保持される。
- **閲覧制御は二重化して矛盾させない**: (i) Plugin経由のダウンロード = Kitsu権限、(ii) Drive直接アクセス = 共有Driveのメンバー（Googleグループ）。Pluginは (ii) を**作らず・同期せず**、(i) と (ii) の差を「検証可能な範囲」で警告するだけにする。
- 管理者設定「個人アカウント連携の許可/禁止」は、PR #19 の `plugin_vfx_files_production_output_setting` 相当の **Production単位の設定1項目**（既存テーブルへの列追加で足りる見込み）。禁止時は個人連携の開始・Picker・原本取得APIを全てサーバー側で拒否。
- 個人連携の認可状態は、試作の `user_oauth.py` が示す「Kitsuユーザー × Google subject、暗号化grant」を**再レビューした上で**再利用候補とする（採用は未決）。既存 `CredentialKeyring`・単回使用state・PKCE・固定callback は再利用できる。

### 既存のProduction用OAuthを個人認証へ流用できるか
**できない（流用しない）。** 既存の state/intent は Production＋接続に束縛され、grant は接続に1つ。個人用は Kitsuユーザー単位の別 intent・別保存・別 scope 検証が必要。同じ OAuth **クライアント**（client ID/secret・暗号鍵・callback基盤）の再利用だけが妥当。

## 4. 権限表（提案）

| 操作 | 一般メンバー | Output登録者 | Production管理者 | Google側で必要な権限 |
|---|---|---|---|---|
| 自分のGoogleを連携/解除 | ○（許可設定時） | ○ | ○（許可/禁止の設定も） | 本人のGoogleアカウントの `drive.file` 同意 |
| 個人Driveの原本を選択（Picker） | ○ | ○ | ○ | 本人トークン。選択ファイルのみ |
| 選択ファイルをOutputとして取り込み | Taskへの `publish` 権限者 | ○ | ○ | 読み取り=個人トークン、書き込み=Production認証情報（共有Driveの Contributor 以上） |
| Outputの閲覧/ダウンロード（Plugin経由） | Task read 権限者 | ○ | ○ | Production認証情報の読み取り（Driveの個人ACLは不問） |
| Driveで直接閲覧 | — | — | — | 共有Driveのメンバー（Googleグループ）。Plugin管理外 |
| Outputの完全削除 | 不可 | 設定が「登録者＋管理者」のとき自分の分 | ○ | Production認証情報が共有Driveの **Manager（Organizer）** |
| 削除権限ポリシー変更 / 個人連携の許可設定 | 不可 | 不可 | ○ | なし（Kitsu判定） |
| 共有Drive・Googleグループの管理 | 不可 | 不可 | Google管理者/Manager（Plugin外） | Google側のみ |

Google Driveの閲覧権限とPlugin上の変更・削除権限は**別軸**: Plugin操作の可否は常にKitsu＋登録者IDで決め、Driveロールは「Production認証情報が実行できるか」の前提条件に過ぎない。

## 5. 現行コードで対応済み / 新規実装が必要

| 対応済み（再利用） | 新規実装が必要 |
|---|---|
| OAuth基盤（`CredentialKeyring`、PKCE/単回使用state、固定callback、暗号化保存、失効Job） | 個人用intent・grant保存（Kitsuユーザー単位）。試作の再レビュー |
| Production保存先の選択・検証・権限失効処理 | Picker統合（トークンの扱い方針決定が前提）と、選択IDのサーバー再検証 |
| Worker経由の download→upload、SHA-256検証、再試行/復旧 | 取り込みで「読み取り=個人トークン / 書き込み=Production」の2アダプタ化 |
| サーバー側のKitsu権限判定、CSRF、Origin検証 | 管理者設定「個人連携 許可/禁止」と各APIでの強制 |
| 共有リンクを作らない・権限を触らない設計 | 作成ファイルに `anyone` 権限が無いことの検査（`permissions.list`）、共有Drive `restrictions` の検証（スコープ要確認） |
| PR #19: 削除ポリシー・完全削除・失敗時fail closed | 連携解除/離脱時の個人grant失効と、保留中取り込みの扱い |

## 6. 解除・離脱・失効時の動作（提案仕様）

- **個人連携の解除**: 個人grantを暗号化領域から削除し Google へ revoke（既存 revoke Job と同型）。**既に取り込んだOutputは共有Driveに残り影響なし**（Production認証情報で保管されているため）。進行中の取り込みは `blocked_authorization` で停止し再実行可能。
- **Productionから離脱/ロール失効**: Kitsu判定が即時に効く（Plugin操作・ダウンロードは拒否）。個人grantはその Production では使わない（取り込みAPIで毎回Kitsu権限を再判定）。grant自体の失効は本人操作または管理者の一括無効化で行う。
- **Production認証情報の失効**: 現行どおり接続を無効化。書き込み・削除は fail closed、閲覧のみ停止。
- **Googleグループ変更**: Plugin は関知しない。Plugin経由の閲覧はKitsu権限で継続するため、Driveのメンバーから外れた人がPlugin経由では閲覧できてしまう**乖離**がありうる（§8）。

## 7. EXR連番・複数ファイル・フォルダ

- 現行は選択ファイルを1つずつ検証・コピーし、フレーム番号は明示入力、`MAX 10000` ファイル。ファイルごとにPickerで渡す必要があるため、**数百〜数千フレームの個別選択は現実的でない**。
- フォルダ選択でその子に `drive.file` が及ぶか、Pickerが複数選択を何件まで返すかは**公式文書で確認できず（要実機検証）**。子に及ばないなら、連番取り込みは「Picker選択」では成立せず、(a)原本を allowed root 配下にアップロードしてから取り込む、(b)`drive.readonly` 等の追加スコープ（制限スコープ・審査要）、のどちらかになる。**ここが最大の製品判断**（§9）。
- フォルダは再帰せず、ID列挙＋ユーザー確認の明示リストとして取り込む（PR #19の「再帰削除しない」方針と一致）。

## 8. セキュリティ上の未解決事項

1. **Picker用アクセストークンのブラウザ露出**: 短命トークンのみを返す、またはPickerを使わない代替（allowed root への手動アップロード）。方針決定が必要。
2. **Plugin経由閲覧とDrive ACLの乖離**: Googleグループから除外されても、Kitsuメンバーである限りPlugin経由で閲覧できる。許容するか、ダウンロード前に Drive 側権限を確認するか。
3. **リンク共有の強制は Plugin では保証できない**: 実効性は共有Drive `restrictions`（管理者設定）に依存。`drives.get` が `drive.file` で読めるかは**要実機検証**。読めなければ「管理者の手順書＋`permissions.list`による個別ファイル検査」までが限界。
4. **削除にはProduction認証情報が共有Driveの Manager 必須**。Content manager 以下だと削除は fail closed（`provider_permission_denied`）。接続時に権限（capabilities）を検査して事前警告するか未決。
5. **認証情報アカウントの人的依存**: その1アカウントが退職・停止すると全Drive操作が止まる。サービスアカウント/共有アカウントの方針は人間の決定事項。
6. 試作ブランチの **migration番号衝突**と未レビュー。
7. `packages/worker.py` は folder の MIME を `"folder"` と比較しており、**実Driveの `application/vnd.google-apps.folder` では取り込みが常に `reconcile_required` になる疑い**（FakeStorageでのみ検証済み）。PR #19 の削除側では修正済み。取り込み側は別修正が必要で、M2の前提になる（実Drive統合試験で確認）。
8. 実Google Drive / Workspace 共有Drive / マイドライブ共有フォルダの検証は未実施。両者は別構成として扱い、同一視しない（Shared Drive の権限は Drive 側ロール、マイドライブ共有フォルダは個別 `permissions` 継承で、`allowed_root` の検証経路も異なる）。

## 9. 最小実装マイルストーン（案）

- **M2-0（実機スパイク・実装なし）**: 隔離した検証用Googleアカウント2つ＋検証用Workspace共有Driveで、(a)Picker選択フォルダの子へのアクセス、(b)`drive.file` での `drives.get`/`permissions.list`、(c)個人アカウントの共有Driveメンバー可否、(d)Manager以外での `files.delete` 失敗形、(e)実Driveでのフォルダ取り込み(MIME)を確認。
- **M2-1**: 個人連携の許可/禁止設定＋個人grant保存（試作を再レビューして必要最小限のみ採用、migration番号整理）。
- **M2-2**: 原本1ファイルのPicker取り込み（読み=個人、書き=Production）。権限失効・解除のテスト。
- **M2-3**: 連番/複数ファイル（M2-0の結果次第で方式決定）。
- **M2-4**: 共有設定検査（`permissions.list`、可能なら `restrictions`）と管理者向け警告表示。

## 10. 実装前に人間が決める事項

1. 個人原本の取り込み方式: Picker（トークンのブラウザ露出を許容）／allowed rootへの手動アップロード／追加スコープ（審査要）。
2. 連番・フォルダ取り込みを製品要件にするか（Pickerで成立しない可能性）。
3. Production認証情報を誰のアカウントにするか（個人管理者／共有・サービス用アカウント）、共有Driveの Manager 権限の付与方針。
4. Plugin経由閲覧とDrive ACLの乖離の許容、またはダウンロード時のDrive権限確認の要否。
5. 非Workspaceの個人アカウントを共有Driveへ招待する運用を許すか（外部共有設定）。
6. 個人grant失効時に保留中の取り込みをどう扱うか。
7. 試作ブランチ（未コミット）を採用ベースにするか、破棄して再設計するか。
