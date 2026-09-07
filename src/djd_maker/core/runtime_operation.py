"""Run-local progress events; contains no browser data or prompt contents."""
from contextlib import contextmanager
from contextvars import ContextVar

_sink = ContextVar('runtime_sink', default=None)

LABELS = {
    'save.deferred': '状態保存待ち・他job続行', 'save.retry': '状態保存を再試行',
    'save.recovered': '状態保存を復旧', 'save.unresolved': '状態保存保留',
    'save.summary': '保存保留を含む処理結果',
    'zip.publish': '検証済みZIPを公開',
    'phase.a': '動画生成開始フェーズ', 'phase.b': '動画回収・変換フェーズ',
    'REMOTE_ARTIFACT_READY': '動画完成・回収待ち',
    'notebook.create': 'Notebook作成中', 'notebook.created': 'Notebook作成完了',
    'source.upload': 'ソースアップロード中', 'source.uploaded': 'ソースアップロード完了',
    'source.error': 'ソース読み込みエラー', 'chat.sent': 'プリセット送信完了',
    'quota.detected': 'クレジット不足', 'reservation.start': '予約処理中', 'reservation.complete': '予約済み',
    'generation.accepted': '動画生成中',
    'download.complete': '動画回収完了', 'raw.validate': 'RAW安全検証中',
    'artifact.deleted': '動画artifact削除完了', 'ending.complete': 'Ending処理完了',
    'hls.complete': 'HLS変換完了', 'zip.complete': 'ZIP作成完了',
    'pause': '一時停止', 'resume': '再開',
    'artifact.ready': '動画完成を確認', 'READY': 'artifact ready（動画完成）',
    'raw.saved': 'RAW保存・安全検証完了', 'ending.skip': 'Ending未選択のためスキップ',
    'ending.start': 'Ending結合', 'hls.start': 'HLS変換', 'zip.start': 'ZIP作成',
    'SOURCE_ALREADY_READY': 'ソース確認済み',
    'preflight': '認証・プロファイル・Notebookホーム画面を確認',
    'WAITING': '生成処理待ち', 'WAITING_VIDEO': '動画生成待ち',
    'GENERATING': '動画生成中', 'COMPLETED': '完了', 'FAILED': 'エラー',
    'DOWNLOAD_PENDING': 'ダウンロードへ進みます', 'DOWNLOADING': 'ダウンロード中',
    'RAW_READY': 'RAW検証済み', 'ENDING': 'Ending処理', 'HLS_ENCODING': 'HLS変換',
    'ZIPPING': 'ZIP作成', 'UPLOADING': 'ソース・Preset処理',
    'RESERVED_WAITING_CREDIT_RESET': '予約待機', 'RECOVERY_PENDING': '回収再確認待ち',
    'job.start': 'ジョブを開始', 'resume.check': '既存Notebookの状態確認',
    'resume.decision': '再開位置を保存', 'state.saved': '工程の状態を保存',
    'job.result': '判定結果を保存', 'job.next': '次のジョブへ',
    'notebook.goto': 'Notebookを開いています', 'notebook.goto.complete': 'Notebook画面を読み込みました',
    'notebook.submit': 'ソース・Preset処理を開始',
    'source.check': 'ソース状態確認', 'source.wait': 'ソース読み込み完了待ち',
    'source.ready': 'ソース確認済み', 'chat.interactable': '中央Chat入力欄を準備',
    'chat.send': 'プリセット送信中', 'chat.reply': 'Notebook返信待ち',
    'chat.retry': 'Preset送信前の返信・状態確認',
    'artifact.poll': '動画の完成状態を確認', 'download.start': '完成動画をダウンロード',
    'media.start': 'RAWからEnding・HLS・ZIP処理',
    'modal.found': 'Notebook案内画面を検出', 'modal.dismiss': '案内画面を閉じています',
    'modal.ready': 'Notebookの操作可能状態を確認',
    'modal.blocked': 'Notebook案内画面を閉じられないため停止',
    'stop.requested': '停止要求を受信', 'stop.wait': '現在処理を終了しています',
    'stop.complete': '停止完了',
    'COMPLETED_SKIP': '完成済み（Notebookは開きません）',
    'GENERATION_ALREADY_STARTED': 'すでに生成中',
    'WAITING_FOR_NEXT_CHECK': '次回確認時刻まで待機',
    'RESERVED_WAITING_RESET': '予約済み・次回確認時刻まで待機',
    'FATAL_FAILED': '自動再開できないエラー',
    'RETRY_REQUIRES_START': 'エラーの内容を確認後、再開操作が必要',
    'REMOTE_STATE_UNKNOWN': 'Notebookの状態を確定できません',
    'NO_OP_JOB_TRANSITION': 'Notebookで処理工程を開始できません',
    'DOWNLOAD_RETRY_REQUIRED': 'ダウンロード検証失敗・再試行操作が必要',
}


def operation_text(value):
    return LABELS.get(str(value), str(value))


def report_operation(stage, **fields):
    if stage not in LABELS:
        return
    sink = _sink.get()
    if sink is not None:
        sink(stage, fields)


@contextmanager
def operation_scope(sink):
    marker = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(marker)
