class URLUtil:
    @staticmethod
    def get_file_name(url):
        start = url.rfind("/")
        end = url.rfind("?")
        if start < 0 and end < 0:
            return url
        if start < 0:
            return url[:end]
        if end < 0:
            return url[start+1:]
        return url[start+1:end]
    
    @staticmethod
    def resolve(base_url, relative_url):
        if relative_url == "./":
            pos = base_url.rfind("/")
            return base_url[:pos]
        return base_url
