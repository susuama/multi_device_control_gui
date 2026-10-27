import { useState } from "react";
import { Workspace } from "./workspace/Workspace";
import type { ViewDefinition } from "./workspace/Workspace";
import { LogView } from "./views/LogView";
import { ApiTestView } from "./views/ApiTestView";
import "./workspace/workspace.css";

// ビューを追加するときは、この一覧へ表示名と表示部品を登録する。
const views: ViewDefinition[] = [
    { id: "log", title: "ログ", render: (panelId) => <LogView panelId={panelId} /> },
    { id: "api-test", title: "APIテスト", render: (panelId) => <ApiTestView panelId={panelId} /> },
];

function App() {
    const [theme, setTheme] = useState("dark");
    return (
        <div className={`application theme-${theme}`}>
            <Workspace views={views} actions={
                <label className="theme-control">
                    配色
                    <select aria-label="配色" value={theme} onChange={(event) => setTheme(event.target.value)}>
                        <option value="dark">ダーク</option><option value="light">ライト</option>
                    </select>
                </label>
            } />
        </div>
    );
}

export default App;
