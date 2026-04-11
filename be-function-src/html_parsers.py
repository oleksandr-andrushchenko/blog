from html.parser import HTMLParser


class FirstPExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_p = False
        self.text_parts = []
        self.found = False

    def handle_starttag(self, tag, attrs):
        if tag == "p" and not self.found:
            self.in_p = True

    def handle_endtag(self, tag):
        if tag == "p" and self.in_p:
            self.in_p = False
            self.found = True  # stop after first <p>

    def handle_data(self, data):
        if self.in_p:
            self.text_parts.append(data)
