class PrivateVariables:
    def __init__(self):
        self.__api_key = ""
        self.__servername = ""
        self.__databasename = ""
        self.__username = ""
        self.__password = ""
        self.__port = None  # optional - each extractor falls back to its DB's default port when None
        self.__extra = {}   # provider-specific fields for Synapse/Databricks/Dynamics365/Snowflake/SAP

    def get_api_key(self):
        return self.__api_key

    def set_api_key(self, key):
        self.__api_key = key
    
    def get_servername(self):
        return self.__servername 
    
    def set_servername(self, server_name):
        self.__servername = server_name

    def get_database_name(self):
        return self.__databasename
    
    def set_database_name(self, database_name):
        self.__databasename = database_name

    def get_username(self):
        return self.__username

    def set_username(self, username):
        self.__username = username

    def get_password(self):
        return self.__password

    def set_password(self, password):
        self.__password = password

    def get_port(self):
        return self.__port

    def set_port(self, port):
        self.__port = port

    def get_extra(self, key, default=None):
        return self.__extra.get(key, default)

    def set_extra(self, key, value):
        self.__extra[key] = value

    def set_extra_dict(self, extra: dict):
        self.__extra.update(extra or {})

    def get_extra_dict(self):
        return dict(self.__extra)