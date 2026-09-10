"""Shared lifecycle reasons: waiting and pause are not terminal stops."""
from dataclasses import dataclass, asdict

MESSAGES = {
    'ALL_TASKS_COMPLETED':'全行程が完了したため停止しました。',
    'USER_STOP':'ユーザーが停止ボタンを押したため停止しました。',
    'APP_CLOSE':'アプリ終了操作のため処理を停止しました。',
    'PAUSED':'一時停止しています。開始操作で同じ位置から再開します。',
    'NO_RUNNABLE_TASK_WAIT':'現在実行可能なjobがないため、次回確認まで待機しています。',
    'TERMINAL_GLOBAL_ERROR':'致命的なエラーのため停止しました。',
    'AUTH_REQUIRED':'Google認証が必要なため処理を停止しました。',
    'BROWSER_CONFLICT':'専用Chromeが使用中のため処理を開始できませんでした。',
    'CONFIG_FATAL':'設定エラーのため停止しました。',
    'STORAGE_FATAL':'保存領域の致命的なエラーのため停止しました。',
    'RECOVERY_CHECK_FINISHED':'今回の未回収確認を終了しました。未完了jobは保持しています。',
}

@dataclass(frozen=True)
class StopReason:
    code: str
    message: str

    @classmethod
    def make(cls,code,detail=''):
        return cls(code,MESSAGES[code]+(' '+detail if detail else ''))

    def to_dict(self):
        return asdict(self)

def exception_reason(error):
    from djd_maker.adapters.browser import BrowserAuthenticationRequired, AuthChromeStillRunning, BrowserProfileLocked
    if isinstance(error,BrowserAuthenticationRequired): return 'AUTH_REQUIRED'
    if isinstance(error,(AuthChromeStillRunning,BrowserProfileLocked)): return 'BROWSER_CONFLICT'
    if isinstance(error,(ValueError,FileNotFoundError,LookupError)): return 'CONFIG_FATAL'
    if isinstance(error,OSError): return 'STORAGE_FATAL'
    return 'TERMINAL_GLOBAL_ERROR'
