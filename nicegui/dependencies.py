from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, Iterable, List, Optional, Set, Tuple

import vbuild

from .dataclasses import KWONLY_SLOTS
from .helpers import hash_file_path
from .version import __version__

if TYPE_CHECKING:
    from .element import Element


@dataclass(**KWONLY_SLOTS)
class Component:
    key: str
    name: str
    path: Path

    @property
    def tag(self) -> str:
        """The tag of the component."""
        return f'nicegui-{self.name}'


@dataclass(**KWONLY_SLOTS)
class VueComponent(Component):
    html: str
    script: str
    style: str


@dataclass(**KWONLY_SLOTS)
class JsComponent(Component):
    pass


@dataclass(**KWONLY_SLOTS)
class Resource:
    key: str
    path: Path


@dataclass(**KWONLY_SLOTS)
class DynamicResource:
    name: str
    function: Callable


@dataclass(**KWONLY_SLOTS)
class Library:
    key: str
    name: str
    path: Path
    expose: bool


vue_components: Dict[str, VueComponent] = {}
js_components: Dict[str, JsComponent] = {}
libraries: Dict[str, Library] = {}
resources: Dict[str, Resource] = {}
dynamic_resources: Dict[str, DynamicResource] = {}


def register_vue_component(path: Path, *, max_time: Optional[float]) -> Component:
    """Register a .vue or .js Vue component.

    Single-file components (.vue) are built right away
    to delegate this "long" process to the bootstrap phase
    and to avoid building the component on every single request.
    """
    key = compute_key(path, max_time=max_time)
    name = _get_name(path)
    if path.suffix == '.vue':
        if key in vue_components and vue_components[key].path == path:
            return vue_components[key]
        assert key not in vue_components, f'Duplicate VUE component {key}'
        v = vbuild.VBuild(path.name, path.read_text(encoding='utf-8'))
        vue_components[key] = VueComponent(key=key, name=name, path=path, html=v.html, script=v.script, style=v.style)
        return vue_components[key]
    if path.suffix == '.js':
        if key in js_components and js_components[key].path == path:
            return js_components[key]
        assert key not in js_components, f'Duplicate JS component {key}'
        js_components[key] = JsComponent(key=key, name=name, path=path)
        return js_components[key]
    raise ValueError(f'Unsupported component type "{path.suffix}"')


def register_library(path: Path, *, expose: bool = False, max_time: Optional[float]) -> Library:
    """Register a *.js library."""
    key = compute_key(path, max_time=max_time)
    name = _get_name(path)
    if path.suffix in {'.js', '.mjs'}:
        if key in libraries and libraries[key].path == path:
            return libraries[key]
        assert key not in libraries, f'Duplicate js library {key}'
        libraries[key] = Library(key=key, name=name, path=path, expose=expose)
        return libraries[key]
    raise ValueError(f'Unsupported library type "{path.suffix}"')


def register_resource(path: Path, *, max_time: Optional[float]) -> Resource:
    """Register a resource."""
    key = compute_key(path, max_time=max_time)
    if key in resources and resources[key].path == path:
        return resources[key]
    assert key not in resources, f'Duplicate resource {key}'
    resources[key] = Resource(key=key, path=path)
    return resources[key]


def register_dynamic_resource(name: str, function: Callable) -> DynamicResource:
    """Register a dynamic resource which returns the result of a function."""
    dynamic_resources[name] = DynamicResource(name=name, function=function)
    return dynamic_resources[name]


@functools.lru_cache(maxsize=None)
def compute_key(path: Path, *, max_time: Optional[float]) -> str:
    """Compute a key for a given path using a hash function.

    If the path is relative to the NiceGUI base directory, the key is computed from the relative path.
    """
    NICEGUI_BASE = Path(__file__).parent
    try:
        rel_path = path.relative_to(NICEGUI_BASE)
    except ValueError:
        rel_path = path
    if path.is_file():
        return f'{hash_file_path(rel_path.parent, max_time=max_time)}/{path.name}'
    return hash_file_path(rel_path, max_time=max_time)


def _get_name(path: Path) -> str:
    return path.name.split('.', 1)[0]


def generate_resources(prefix: str, elements: Iterable[Element]) -> Tuple[List[str],
                                                                          List[str],
                                                                          List[str],
                                                                          Dict[str, str],
                                                                          List[str],
                                                                          List[str]]:
    """Generate the resources required by the elements to be sent to the client."""
    done_libraries: Set[str] = set()
    done_components: Set[str] = set()
    vue_scripts: List[str] = []
    vue_html: List[str] = []
    vue_styles: List[str] = []
    imports: Dict[str, str] = {}
    js_imports: List[str] = []
    js_imports_urls: List[str] = []

    # build the importmap structure for exposed libraries
    for key, library in libraries.items():
        if key not in done_libraries and library.expose:
            imports[library.name] = f'{prefix}/_nicegui/{__version__}/libraries/{key}'
            done_libraries.add(key)

    # build the none-optimized component (i.e. the Vue component)
    for key, vue_component in vue_components.items():
        if key not in done_components:
            vue_html.append(vue_component.html)
            vue_scripts.append(vue_component.script.replace(f"Vue.component('{vue_component.name}',",
                                                            f"app.component('{vue_component.tag}',", 1))
            vue_styles.append(vue_component.style)
            done_components.add(key)

    # build the resources associated with the elements
    for element in elements:
        for library in element.libraries:
            if library.key not in done_libraries:
                if not library.expose:
                    url = f'{prefix}/_nicegui/{__version__}/libraries/{library.key}'
                    js_imports.append(f'import "{url}";')
                    js_imports_urls.append(url)
                done_libraries.add(library.key)
        if element.component:
            js_component = element.component
            if js_component.key not in done_components and js_component.path.suffix.lower() == '.js':
                url = f'{prefix}/_nicegui/{__version__}/components/{js_component.key}'
                js_imports.append(f'import {{ default as {js_component.name} }} from "{url}";')
                js_imports.append(f'app.component("{js_component.tag}", {js_component.name});')
                js_imports_urls.append(url)
                done_components.add(js_component.key)
    return vue_html, vue_styles, vue_scripts, imports, js_imports, js_imports_urls
