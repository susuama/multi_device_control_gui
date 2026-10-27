import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { LOG_LIMIT, logStore } from "../logging/logStore";

/** 共通ログを表示する。表示件数とスクロール追従の設定は、このビューだけで保持する。 */
export function LogView({ panelId }: { panelId: string }) {
    const entries = useSyncExternalStore(logStore.subscribe, logStore.getSnapshot);
    const [displayLimit, setDisplayLimit] = useState(LOG_LIMIT);
    const [followTail, setFollowTail] = useState(true);
    const textArea = useRef<HTMLTextAreaElement>(null);
    const text = useMemo(() => entries.slice(-displayLimit).map((entry) => {
        const time = new Date(entry.timestamp).toLocaleTimeString("ja-JP", { hour12: false });
        return `${time} ${entry.level.padEnd(5)} ${entry.source.padEnd(10)} ${entry.message}`;
    }).join("\n"), [entries, displayLimit]);

    useEffect(() => {
        if (followTail && textArea.current) textArea.current.scrollTop = textArea.current.scrollHeight;
    }, [text, followTail]);

    return (
        <div className="log-view">
            <div className="view-toolbar">
                <span className="view-note">{Math.min(entries.length, displayLimit)} / {entries.length}件を表示</span>
                <label className="log-display-limit">
                    表示件数
                    <select aria-label={`${panelId}のログ表示件数`} value={displayLimit}
                        onChange={(event) => setDisplayLimit(Number(event.target.value))}>
                        {[10, 50, 100, 500, LOG_LIMIT].map((limit) => <option key={limit} value={limit}>{limit}件</option>)}
                    </select>
                </label>
                <label className="follow-tail">
                    <input type="checkbox" checked={followTail} onChange={(event) => setFollowTail(event.target.checked)} />
                    末尾を追従
                </label>
                <button type="button" onClick={logStore.clear} disabled={entries.length === 0}>クリア</button>
            </div>
            <textarea ref={textArea} className="log-text" aria-label={`${panelId}のログ`} value={text} readOnly spellCheck={false} wrap="off"
                placeholder="ログはありません。APIテストからリクエストを送信すると、通信結果をここに表示します。" />
        </div>
    );
}
