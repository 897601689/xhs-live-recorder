"""静态自检：跑不了真实界面时的兜底检查

查两类最容易出的错：
1. self.X 读到了这个类里从没赋值过的数据属性（漏建属性 = 一用到就 AttributeError）
2. 代码里引用的名字在本模块里根本不存在（改名 / 删代码 / 跨模块漏加前缀留下的悬空引用）
"""
import ast
import builtins
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = ([ROOT / "main.py"] + sorted((ROOT / "xhs").glob("*.py"))
         + sorted((ROOT / "xhs" / "ui").glob("*.py")))

QT_BASE = {
    # QWidget / QMainWindow / QObject 自带的成员，源码里当然找不到赋值
    "setStyleSheet", "setObjectName", "objectName", "setLayout", "setWindowTitle", "setMinimumSize",
    "resize", "show", "hide", "close", "update", "repaint", "setVisible", "setGeometry",
    "move", "width", "height", "size", "font", "setFont", "setCursor", "setFocusPolicy",
    "setToolTip", "setText", "text", "isEnabled", "setEnabled", "setContentsMargins",
    "setSpacing", "addWidget", "addLayout", "addItem", "setAlignment", "installEventFilter",
    "eventFilter", "parent", "setParent", "deleteLater", "findChildren", "setCentralWidget",
    "setWindowFlags", "setWindowFlag", "windowFlags", "setAttribute", "setFixedSize",
    "setFixedHeight", "setFixedWidth",
    "setMinimumWidth", "setMaximumHeight", "mapToGlobal", "rect", "setMouseTracking",
    "setAutoFillBackground", "setGraphicsEffect", "sizeHint", "setAccessibleName",
    "setSizePolicy", "setFrameShape", "setFocus", "clearFocus", "hasFocus", "style", "window",
    "windowHandle", "windowTitle", "setMask", "setDisabled", "minimumSize", "maximumHeight",
    "setContextMenuPolicy", "setWordWrap", "setTabOrder", "setPlaceholderText",
    "setCurrentIndex", "currentIndex", "count", "maxVisibleItems", "setCheckable",
    "setChecked", "isChecked",
    "grab", "setPixmap", "setHidden", "isVisible", "isMinimized", "isMaximized",
    "showNormal", "showMaximized", "showMinimized", "raise_", "activateWindow",
    "setInputMethodHints", "setLineWrapMode", "setReadOnly", "setFrameStyle", "viewport",
    "document", "verticalScrollBar", "horizontalScrollBar", "setWidget",
    "setWidgetResizable", "setCursorShape", "dumpObjectInfo", "setAccessibleDescription",
    "unsetProperty", "property", "setProperty", "itemText", "currentIndexChanged",
    "children", "childrenRect", "pos", "x", "y", "normalGeometry",
    "setWindowOpacity", "testAttribute", "maximumWidth", "setMaximumWidth", "layout",
    "setLayoutDirection", "mapTo", "mapFrom", "isEnabledTo", "setMinimumHeight",
    "minimumHeight", "contentsMargins", "frameGeometry", "geometry", "setUrl", "set_url",
    "devicePixelRatioF", "mapFromGlobal", "winId", "screen", "mask",
    # QDialog 自带：确认框自己 accept/reject，居中要找父窗
    "accept", "reject", "setModal", "parentWidget",
}

# 信号名（Signal 赋值）+ 方法参数里常见的 Qt 内建，用不到的属性一律放过
IGNORE_ATTRS = {"connected", "connect", "disconnect", "emit"}


def store_names(node, names):
    for target in getattr(node, "targets", []) or []:
        for sub in ast.walk(target):
            if isinstance(sub, ast.Name):
                names.add(sub.id)


def collect_args(node, names):
    args = node.args
    for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        names.add(arg.arg)
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)


def defined_names(tree):
    """本模块内所有「可能存在的名字」：定义、导入、局部赋值、参数、global 声明"""
    names = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            if not isinstance(node, ast.ClassDef):
                collect_args(node, names)
        elif isinstance(node, ast.Lambda):
            collect_args(node, names)
        elif isinstance(node, ast.Assign):
            store_names(node, names)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.NamedExpr):
            store_names(node, names)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            store_names(node, names)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    store_names(item, names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.comprehension):
            store_names(node, names)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
    return names


TREES = {}
for path in FILES:
    TREES[path] = ast.parse(path.read_text(encoding="utf-8"))

# 兄弟类里定义过的方法 / 属性也算（继承关系不在这个脚本里解析，宁可放过不可误报）
anywhere_stored = set()
for tree in TREES.values():
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            anywhere_stored.add(node.name)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            anywhere_stored.add(node.attr)
        elif isinstance(node, ast.Assign):
            store_names(node, anywhere_stored)

problems = []
unknown_all = set()
PACKAGE_DIRS = {ROOT / "xhs", ROOT / "xhs" / "ui"}
MODULE_NAMES = {path.stem for path in FILES if path.parent in PACKAGE_DIRS}
DEFINED_PER_MODULE = {path.stem: defined_names(tree)
                      for path, tree in TREES.items() if path.parent in PACKAGE_DIRS}
for path, tree in TREES.items():
    known = defined_names(tree)
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        assigned = set(QT_BASE) | IGNORE_ATTRS | anywhere_stored
        for sub in ast.walk(cls):
            if isinstance(sub, ast.Attribute) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                assigned.add(sub.attr)
        for sub in ast.walk(cls):
            if (isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Load)
                    and isinstance(sub.value, ast.Name) and sub.value.id == "self"
                    and sub.attr not in assigned):
                problems.append((path.name, cls.name, sub.attr, sub.lineno))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in known:
            unknown_all.add(f"{path.name}:{node.lineno} {node.id}")
        # 跨模块引用（settings.xxx / recorder.xxx）也要落到真实存在的名字上
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in MODULE_NAMES and node.value.id != path.stem
                and node.attr not in DEFINED_PER_MODULE[node.value.id]):
            unknown_all.add(f"{path.name}:{node.lineno} {node.value.id}.{node.attr}")

for module, cls_name, attr, lineno in sorted(problems):
    print(f"  ? {module} {cls_name}: self.{attr} 在第 {lineno} 行被读取，类里从没赋值")
print(f"self 属性可疑读取: {len(problems)} 处")
print(f"本模块找不到定义的名字: {sorted(unknown_all) or '无'}")
sys.exit(1 if (problems or unknown_all) else 0)
