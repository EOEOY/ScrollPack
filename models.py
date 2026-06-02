class Novel:
    def __init__(self):
        self.url = None
        self.id = ""
        self.title = ""
        self.alias = None
        self.author = ""
        self.status = ""
        self.cover_url = None
        self.tags = []
        self.publisher = None
        self.description = None
    
    def __str__(self):
        lines = [self.title]
        if self.alias:
            lines[-1] += f"({self.alias})"
        lines.append(f"作者: {self.author}")
        lines.append(f"状态: {self.status}")
        if self.tags:
            lines.append(f"标签: {', '.join(self.tags)}")
        if self.description:
            lines.append(self.description)
        return "\n".join(lines)

class Catalog:
    def __init__(self, novel):
        self.novel = novel
        self.volumes = []

class Volume:
    def __init__(self, volume_name, catalog):
        self.volume_name = volume_name
        self.catalog = catalog
        self.chapters = []
        self.cover = None
    
    def __str__(self):
        return self.volume_name if self.volume_name else self.catalog.novel.title
    
    def __eq__(self, other):
        return isinstance(other, Volume) and self.volume_name == other.volume_name
    
    def __hash__(self):
        return hash(self.volume_name)

class Chapter:
    def __init__(self, chapter_name, chapter_url, volume):
        self.chapter_name = chapter_name
        self.chapter_url = chapter_url
        self.chapter_content = None
        self.volume = volume
    
    def __hash__(self):
        return hash((self.chapter_name, self.chapter_url, hash(self.volume)))
    
    def __eq__(self, other):
        if not isinstance(other, Chapter):
            return False
        return hash(self) == hash(other)
