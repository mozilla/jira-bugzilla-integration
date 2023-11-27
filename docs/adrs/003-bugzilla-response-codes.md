# Bugzilla disables webhooks after too many errors

- Status: proposed
- Date: 2023-27-11

Tracking issue: 

## Context and Problem Statement
When bugzilla receives too many error responses from JBI, it stops triggering webhook calls for the entire project causing data to stop syncing. Frequently these errors are due to JBI being unable to process a payload due to errors in configuration (or incomplete configuration) in Jira or a mismatch of data for a single bug. 

We don't want the entire sync process to stop because of this. We have identified four options to solve this problem.

## Decision Drivers

- Amount of initial engineering effort
- Amount of maintenance effort
- Overall performance of JBI (how quickly is data able to move)
- How intuitive the solution is to the users that depend on the data (will picking the easiest option solve their needs?)

## Considered Options
For all of these options, we will be returning a succesful response to bugzilla's webhook calls. I propose a 202 status code to identify that we're accepting the request immediately and are processing it asynchronously.

### Option 1: Log the failure and move on
JBI will log that we couldn't process a specific payload, along with relevant ID's (bug id, jira ticket id, comment id, etc) so further investigation can be done if needed.

Pros:
- The simplest solution
- Very low effort

Cons: 
- Will not alert people to data loss (without additional alerting functionality)
- Still requires engineers to investigate further

### Option 2: Ask a human to do something
JBI will alert users that data could not be sync'd. This could happen through an IM alert or an email immediately, or a scheduled (daily?) report.

### Option 3: Queue retries internally
Create a persistence layer within the JBI containers that will queue and retry jobs for a specific length (2 hours?) of time. This could be done with an internal cache (redis) or database (postgres) within the container. After retries exceed the max time length, an error would be logged and the data would be dropped.

### Option 4: Queue data externally
Similar to option 3, but instead of JBI queing the events we would have a dedicated service that accepts all API calls from bugzilla and puts them into a queue. JBI would shift to being a downstream service and process these events asynchronously. If events fail to process to many times, they would get sent to a DLQ (dead letter queue) that could be replayed later if needed.

There are plenty of existing solutions we could use to solve this problem from a technical perspective. A seperate ADR would be done to identify the best answer if we choose to go this route.

## Option 5: A combination of the above
Example: We could create an external queue for processing and then alert users if the DLQ grows to quickly..


## Decision Outcome

Pending discussion

### Positive Consequences <!-- optional -->

Pending discussion

### Negative Consequences <!-- optional -->

Pending discussion

## Pros and Cons of the Options <!-- optional -->

### Option 1: Log the failure and move on
Pros:
- The simplest solution
- Very low effort

Cons: 
- Will not alert people to data loss (without additional alerting functionality)
- Still requires engineers to investigate further

### Option 2: Ask a human to do something
Pros:
- Removes need for engineering
- Alerts users directly that there is a problem
- Moderate effort

Cons:
- Alerts can be noisy and cause notification fatigue

### Option 3: Queue retries internally
Pros:
- Allows for retries up to a designated amount of time
- Keeping all services within the container make dev work easier compared to an external queue

Cons:
- Increases complexity of the containers
- Data will not persist container restarts
- High effort

### Option 4: Queue data externally
Pros:
- Most durable solution

Cons:
- Most complex solution
- Highest effort

## Links 
- [What is event streaming?](https://kafka.apache.org/documentation/#intro_streaming) - Documentation from Apache Kafka
