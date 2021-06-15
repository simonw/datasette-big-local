class Tracker:
    def __init__(self):
        self.trackers = {}

    def wrap(self, iterator):
        i = 0
        for row in iterator:
            i += 1
            if i % 100 == 0:
                print(i)
            for key, value in row.items():
                tracker = self.trackers.setdefault(key, ValueTracker())
                tracker.evaluate(value)
            yield row

    @property
    def types(self):
        return {key: tracker.guessed_type for key, tracker in self.trackers.items()}


class ValueTracker:
    def __init__(self):
        self.couldbe = {key: getattr(self, "test_" + key) for key in self.get_tests()}

    @classmethod
    def get_tests(cls):
        return [
            key.split("test_")[-1]
            for key in cls.__dict__.keys()
            if key.startswith("test_")
        ]

    def test_integer(self, value):
        try:
            int(value)
            return True
        except ValueError:
            return False

    def test_float(self, value):
        try:
            float(value)
            return True
        except ValueError:
            return False

    def __repr__(self):
        return self.guessed_type + ": possibilities = " + repr(self.couldbe)

    @property
    def guessed_type(self):
        options = set(self.couldbe.keys())
        # Return based on precedence
        for key in self.get_tests():
            if key in options:
                return key
        return "text"

    def evaluate(self, value):
        if not value or not self.couldbe:
            return
        not_these = []
        for name, test in self.couldbe.items():
            if not test(value):
                not_these.append(name)
        for key in not_these:
            del self.couldbe[key]
