class VectorStoreRouter:
    route_app_labels = {"vector_store"}
    vector_alias = "vector"
    default_alias = "default"

    def db_for_read(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return self.vector_alias
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return self.vector_alias
        return None

    def allow_relation(self, obj1, obj2, **hints):
        labels = {obj1._meta.app_label, obj2._meta.app_label}
        if labels & self.route_app_labels:
            return labels <= self.route_app_labels
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.route_app_labels:
            return db == self.vector_alias
        if db == self.vector_alias:
            return False
        return None
