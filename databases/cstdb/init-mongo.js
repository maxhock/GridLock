// Create the CST metadata database and switch to it
db = db.getSiblingDB('copper');

// Create CST-required collections (federations, scenarios)
// and the custom_metadata collection for user-defined data
// See: https://cst.readthedocs.io/en/stable/Metadata.html
db.scenarios.insert([
  { "collection name": 'scenarios' },
]);

db.federations.insert([
  { "collection name": 'federations' },
]);

db.custom_metadata.insert([
  { "collection name": 'custom_metadata' },
]);

// Create a user with read and write privileges for the database
db.createUser({
  user: 'worker',
  pwd: 'worker',
  roles: [
    { role: 'readWrite', db: 'copper' }
  ]
});
