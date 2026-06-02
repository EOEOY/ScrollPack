class PackArgument:
    def __init__(self, add_chapter_title=True, combine_volume=False, pack_volumes=None, output_format="epub"):
        self.add_chapter_title = add_chapter_title
        self.combine_volume = combine_volume
        self.pack_volumes = pack_volumes or []
        self.output_format = output_format
    
    def __str__(self):
        return f"PackArgument(add_chapter_title={self.add_chapter_title}, combine_volume={self.combine_volume}, pack_volumes={self.pack_volumes})"
