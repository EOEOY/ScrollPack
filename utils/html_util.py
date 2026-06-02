import re
from bs4 import BeautifulSoup, Tag

class HTMLUtil:
    @staticmethod
    def remove_elements(elements):
        for el in elements:
            el.decompose()
    
    @staticmethod
    def remove_line_break(element):
        if hasattr(element, 'children'):
            for child in list(element.children):
                if isinstance(child, Tag):
                    HTMLUtil.remove_line_break(child)
        if hasattr(element, 'string') and element.string:
            element.string = element.string.replace('\n', '')
    
    @staticmethod
    def wrap_duokan_image(element):
        for img in element.find_all('img'):
            wrapper = BeautifulSoup('<div class="duokan-image-single"></div>', 'html.parser').div
            img.wrap(wrapper)
    
    @staticmethod
    def unwrap(element):
        children = list(element.contents)
        parent = element.parent
        for child in children:
            parent.insert(parent.contents.index(element), child)
        element.decompose()
    
    @staticmethod
    def remove_by_pattern(element, pattern, match_id=False, match_tag=True, match_class=False):
        r = re.compile(pattern)
        if not isinstance(element, Tag):
            return
        for child in list(element.children):
            if not isinstance(child, Tag):
                continue
            if match_id and r.search(child.get('id', '')):
                child.decompose()
                continue
            if match_tag and r.search(child.name or ''):
                child.decompose()
                continue
            if match_class and r.search(' '.join(child.get('class', []))):
                child.decompose()
                continue
            HTMLUtil.remove_by_pattern(child, pattern, match_id, match_tag, match_class)
