# Bugzilla disables webhooks after too many errors

- Status: proposed
- Date: 2023-11-27

Tracking issues: 
- [21 - Setting up Webhooks with 'ANY' Product](https://github.com/mozilla/jira-bugzilla-integration/issues/21)
- [82 - Exception in JBI can delay webhook delivery](https://github.com/mozilla/jira-bugzilla-integration/issues/82)
- [181 - Can we ping Bugzilla to see if the webhook is enabled?](https://github.com/mozilla/jira-bugzilla-integration/issues/181)
- [710 - System is able to "drop" erring messages without user interference](https://github.com/mozilla/jira-bugzilla-integration/issues/710)
- [730 - Establish convention for capturing system incidents](https://github.com/mozilla/jira-bugzilla-integration/issues/730)
- [743 - Create alerts for when bugs fail to sync](https://github.com/mozilla/jira-bugzilla-integration/issues/743)

## Context and Problem Statement
When bugzilla receives too many error responses from JBI, it stops triggering webhook calls for the entire project causing data to stop syncing. Frequently, these errors are due to a configuration error in Jira or bugzilla. JBI being unable to process a payload due to errors in configuration (or incomplete configuration) in Jira or a mismatch of data for a single bug. These outages can last multiple days in some cases.

We don't want the entire sync process to stop because of this. We have identified four options to solve this problem.

## Decision Drivers

- Amount of initial engineering effort
- Amount of maintenance effort
- Overall performance of JBI (how quickly is data able to move)
- How intuitive the solution is to the users that depend on the data (will picking the easiest option solve their needs?)

## Considered Options
For all of these options, we will be returning a successful 200 response to bugzilla's webhook calls. Note: we have to return a 200 because of bugzilla's webhook functionality (they check for 200 specifically, not just any OK response).

### Option 1: Log the failure and move on
JBI will log that we couldn't process a specific payload, along with relevant ID's (bug id, jira ticket id, comment id, etc) so further investigation can be done if needed.

**Decision Drivers**
- Amount of initial engineering effort: very low
- Amount of maintenance effort: very low
- Overall performance of JBI: high
- How intuitive the solution: low - users will notice data is missing but see status pages that look green

**Pros:**
- The simplest solution

**Cons:**
- Will not alert people to data loss (without additional alerting functionality)
- Still requires engineers to investigate further if needed

### Option 2: Ask a human to do something
JBI will alert users that data could not be sync'd. This could happen through an IM alert or an email immediately, or a scheduled (daily?) report. We should know which users to identify based on project configuration in bugzilla or a distribution list.

**Decision Drivers**
- Amount of initial engineering effort: low
- Amount of maintenance effort: low
- Overall performance of JBI: high
- How intuitive the solution: high

**Pros:**
- Removes need for engineering to investigate
- Alerts users directly that there is a problem

**Cons:**
- Alerts can be noisy and cause notification fatigue

### Option 3: Queue retries internally
Create a persistence layer within the JBI containers that will queue and retry jobs for a specific length (2 hours? 2 days?) of time. This could be done with an internal cache (redis) or database (postgres) within the container. After retries exceed the max time length, an error would be logged and the data would be dropped.

**Decision Drivers**
- Amount of initial engineering effort: high, creating more complex containers
- Amount of maintenance effort: moderate, increased complexity of testing and debugging
- Overall performance of JBI: high
- How intuitive the solution: low - users will notice data is missing but see status pages that look green

**Pros:**
- Allows for retries up to a designated amount of time
- Keeping all services within the container make end-to-end testing and debugging easier (compared to option 4)

**Cons:**
- Increases complexity of the containers
- Data will not persist container restarts
- High effort for engineers to build and maintain
- Less intuitive to users and engineers, we would need to report on cache/queue metrics to logs

### Option 4: Queue data externally
Similar to option 3, but instead of JBI queing the events we would have a dedicated service that accepts all API calls from bugzilla and puts them into a queue. JBI would shift to being a downstream service and process these events asynchronously. If events fail to process to many times, they would get sent to a DLQ (dead letter queue) that could be replayed later if needed.

There are plenty of existing solutions we could use to solve this problem from a technical perspective. A seperate ADR would be done to identify the best answer if we choose to go this route.

**Decision Drivers**
- Amount of initial engineering effort: high, building out more infrastructure
- Amount of maintenance effort: high, maintaining more infrastructure
- Overall performance of JBI: highest, event driven
- How intuitive the solution: high - we'll have reporting on queue metrics

**Pros:**
- Most durable solution
- Does not reduce JBI throughput
- Intuitive to users and engineers, we can see and report on data in queue

**Cons:**
- Most complex solution
- Highest effort for engineers to build and maintain

## Option 5: A combination of the above
Example: We could create an external queue for processing and then alert users if the DLQ grows too quickly.

## Decision Outcome

Pending discussion

### Positive Consequences <!-- optional -->

Pending discussion

### Negative Consequences <!-- optional -->

Pending discussion

## Links 
- [What is event streaming?](https://kafka.apache.org/documentation/#intro_streaming) - Documentation from Apache Kafka
