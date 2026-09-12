package store

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strconv"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	ddbtypes "github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
	"github.com/aws/smithy-go"

	"linkpulse/internal/metrics"
)

// Options configures the DynamoDB-backed store.
type Options struct {
	Table    string
	Region   string
	Endpoint string // empty means real AWS; set to reach LocalStack
	Shards   int    // CLICK_SHARDS — see docs/data-model.md §3
	TTLDays  int

	// MaxRetries is deliberately low. The SDK retries throttling errors by default,
	// which would absorb ProvisionedThroughputExceededException before the
	// application ever sees it — and chaos experiment 1 asserts on the application's
	// error rate and p99, not only on the CloudWatch throttle counter. Two attempts
	// keeps one retry for genuine transient failures without hiding a sustained
	// throttle behind a wall of backoff.
	MaxRetries int
}

type Dynamo struct {
	c      *dynamodb.Client
	table  string
	shards int
	ttl    time.Duration
	hasTTL bool
}

var _ Store = (*Dynamo)(nil)

func NewDynamo(ctx context.Context, o Options) (*Dynamo, error) {
	if o.Table == "" {
		return nil, errors.New("store: table name is required")
	}
	if o.Shards < 1 {
		o.Shards = 1
	}
	if o.MaxRetries < 1 {
		o.MaxRetries = 2
	}

	loadOpts := []func(*awsconfig.LoadOptions) error{
		awsconfig.WithRegion(o.Region),
		awsconfig.WithRetryMaxAttempts(o.MaxRetries),
	}
	// LocalStack accepts any credentials but the SDK still requires some. Only inject
	// placeholders when nothing real is configured, so this never shadows a real
	// credential chain (IRSA included).
	if o.Endpoint != "" && os.Getenv("AWS_ACCESS_KEY_ID") == "" {
		loadOpts = append(loadOpts, awsconfig.WithCredentialsProvider(
			credentials.NewStaticCredentialsProvider("test", "test", ""),
		))
	}

	cfg, err := awsconfig.LoadDefaultConfig(ctx, loadOpts...)
	if err != nil {
		return nil, fmt.Errorf("store: load aws config: %w", err)
	}

	client := dynamodb.NewFromConfig(cfg, func(po *dynamodb.Options) {
		if o.Endpoint != "" {
			po.BaseEndpoint = aws.String(o.Endpoint)
		}
	})

	return &Dynamo{
		c:      client,
		table:  o.Table,
		shards: o.Shards,
		ttl:    time.Duration(o.TTLDays) * 24 * time.Hour,
		hasTTL: o.TTLDays > 0,
	}, nil
}

// Shards reports the configured shard count so the process can publish it as a metric.
// Dashboards read it to show which side of the experiment-1 flip a run is on.
func (d *Dynamo) Shards() int { return d.shards }

// ---------- A2: create ----------

func (d *Dynamo) CreateLink(ctx context.Context, in CreateLinkInput) (Link, error) {
	now := time.Now().UTC()
	l := Link{
		Code:      in.Code,
		LongURL:   in.LongURL,
		OwnerID:   in.OwnerID,
		CreatedAt: now,
		Active:    true,
	}

	item := map[string]ddbtypes.AttributeValue{
		"PK":        s(linkPK(l.Code)),
		"SK":        s(skMeta),
		"type":      s("LINK"),
		"code":      s(l.Code),
		"longUrl":   s(l.LongURL),
		"ownerId":   s(l.OwnerID),
		"createdAt": s(now.Format(time.RFC3339Nano)),
		"active":    &ddbtypes.AttributeValueMemberBOOL{Value: true},
		// Sparse GSI1: only LINK items carry these, so click items cost no index
		// writes (docs/data-model.md §4).
		"GSI1PK": s(userPK(l.OwnerID)),
		"GSI1SK": s(prefixCreat + now.Format(time.RFC3339Nano)),
	}

	_, err := d.call(ctx, "PutItem", func() (any, error) {
		return d.c.PutItem(ctx, &dynamodb.PutItemInput{
			TableName:           aws.String(d.table),
			Item:                item,
			ConditionExpression: aws.String("attribute_not_exists(PK)"),
		})
	})
	if err != nil {
		var cf *ddbtypes.ConditionalCheckFailedException
		if errors.As(err, &cf) {
			return Link{}, ErrCodeTaken
		}
		return Link{}, err
	}
	return l, nil
}

// ---------- A1: resolve (the hot path) ----------

func (d *Dynamo) GetLink(ctx context.Context, code string) (Link, error) {
	out, err := d.call(ctx, "GetItem", func() (any, error) {
		return d.c.GetItem(ctx, &dynamodb.GetItemInput{
			TableName: aws.String(d.table),
			Key: map[string]ddbtypes.AttributeValue{
				"PK": s(linkPK(code)),
				"SK": s(skMeta),
			},
			// Eventually consistent on purpose: half the RCU, and a redirect does
			// not need read-after-write (docs/data-model.md §1, A1).
			ConsistentRead: aws.Bool(false),
		})
	})
	if err != nil {
		return Link{}, err
	}
	res := out.(*dynamodb.GetItemOutput)
	if len(res.Item) == 0 {
		return Link{}, ErrNotFound
	}
	return linkFromItem(res.Item), nil
}

// ---------- A3: record a click ----------

func (d *Dynamo) RecordClick(ctx context.Context, code string, c Click) error {
	if c.At.IsZero() {
		c.At = time.Now().UTC()
	}
	sk, err := clickSK(c.At)
	if err != nil {
		return err
	}

	item := map[string]ddbtypes.AttributeValue{
		"PK":       s(linkPK(code)),
		"SK":       s(sk),
		"type":     s("CLICK"),
		"ts":       s(c.At.UTC().Format(time.RFC3339Nano)),
		"referrer": s(c.Referrer),
		"country":  s(c.Country),
		"uaClass":  s(c.UAClass),
	}
	if d.hasTTL {
		item["expiresAt"] = n(c.At.Add(d.ttl).Unix())
	}

	if _, err := d.call(ctx, "PutItem", func() (any, error) {
		return d.c.PutItem(ctx, &dynamodb.PutItemInput{
			TableName: aws.String(d.table),
			Item:      item,
		})
	}); err != nil {
		return err
	}

	// The aggregate write. This is the one that hot-partitions, and the shard suffix
	// is the mitigation.
	shard := pickShard(d.shards)
	_, err = d.call(ctx, "UpdateItem", func() (any, error) {
		return d.c.UpdateItem(ctx, &dynamodb.UpdateItemInput{
			TableName: aws.String(d.table),
			Key: map[string]ddbtypes.AttributeValue{
				"PK": s(linkPK(code)),
				"SK": s(statSK(dayOf(c.At), shard)),
			},
			UpdateExpression: aws.String("ADD #c :one SET #t = :type"),
			ExpressionAttributeNames: map[string]string{
				"#c": "count",
				"#t": "type",
			},
			ExpressionAttributeValues: map[string]ddbtypes.AttributeValue{
				":one":  n(1),
				":type": s("CLICKSTAT"),
			},
		})
	})
	return err
}

// ---------- A4 / A5: aggregates ----------

func (d *Dynamo) ClickTotal(ctx context.Context, code string) (int64, error) {
	days, err := d.queryStats(ctx, code, "", "")
	if err != nil {
		return 0, err
	}
	var total int64
	for _, dc := range days {
		total += dc.Count
	}
	return total, nil
}

func (d *Dynamo) ClicksByDay(ctx context.Context, code, from, to string) ([]DayCount, error) {
	return d.queryStats(ctx, code, from, to)
}

// queryStats reads CLICKSTAT items and folds the shards back together. Empty from/to
// means every day held for the link.
func (d *Dynamo) queryStats(ctx context.Context, code, from, to string) ([]DayCount, error) {
	in := &dynamodb.QueryInput{
		TableName: aws.String(d.table),
		ExpressionAttributeValues: map[string]ddbtypes.AttributeValue{
			":pk": s(linkPK(code)),
		},
	}
	if from != "" && to != "" {
		in.KeyConditionExpression = aws.String("PK = :pk AND SK BETWEEN :lo AND :hi")
		in.ExpressionAttributeValues[":lo"] = s(statRangeLo(from))
		in.ExpressionAttributeValues[":hi"] = s(statRangeHi(to))
	} else {
		in.KeyConditionExpression = aws.String("PK = :pk AND begins_with(SK, :p)")
		in.ExpressionAttributeValues[":p"] = s(prefixStat)
	}

	byDay := map[string]int64{}
	for {
		out, err := d.call(ctx, "Query", func() (any, error) { return d.c.Query(ctx, in) })
		if err != nil {
			return nil, err
		}
		res := out.(*dynamodb.QueryOutput)
		for _, it := range res.Items {
			day := dayFromStatSK(str(it["SK"]))
			if day == "" {
				continue
			}
			byDay[day] += num(it["count"])
		}
		if len(res.LastEvaluatedKey) == 0 {
			break
		}
		in.ExclusiveStartKey = res.LastEvaluatedKey
	}
	return sortedDays(byDay), nil
}

// ---------- A6: recent click feed ----------

func (d *Dynamo) RecentClicks(ctx context.Context, code string, limit int32) ([]Click, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	out, err := d.call(ctx, "Query", func() (any, error) {
		return d.c.Query(ctx, &dynamodb.QueryInput{
			TableName:              aws.String(d.table),
			KeyConditionExpression: aws.String("PK = :pk AND begins_with(SK, :p)"),
			ExpressionAttributeValues: map[string]ddbtypes.AttributeValue{
				":pk": s(linkPK(code)),
				":p":  s(prefixClick),
			},
			// Newest first straight off the sort key — no application-side sort.
			ScanIndexForward: aws.Bool(false),
			Limit:            aws.Int32(limit),
		})
	})
	if err != nil {
		return nil, err
	}
	res := out.(*dynamodb.QueryOutput)
	clicks := make([]Click, 0, len(res.Items))
	for _, it := range res.Items {
		ts, _ := time.Parse(time.RFC3339Nano, str(it["ts"]))
		clicks = append(clicks, Click{
			At:       ts,
			Referrer: str(it["referrer"]),
			Country:  str(it["country"]),
			UAClass:  str(it["uaClass"]),
		})
	}
	return clicks, nil
}

// ---------- A7: owner's links ----------

func (d *Dynamo) LinksByOwner(ctx context.Context, owner string, limit int32) ([]Link, error) {
	if limit <= 0 || limit > 100 {
		limit = 25
	}
	out, err := d.call(ctx, "Query", func() (any, error) {
		return d.c.Query(ctx, &dynamodb.QueryInput{
			TableName:              aws.String(d.table),
			IndexName:              aws.String("GSI1"),
			KeyConditionExpression: aws.String("GSI1PK = :pk"),
			ExpressionAttributeValues: map[string]ddbtypes.AttributeValue{
				":pk": s(userPK(owner)),
			},
			ScanIndexForward: aws.Bool(false),
			Limit:            aws.Int32(limit),
		})
	})
	if err != nil {
		return nil, err
	}
	res := out.(*dynamodb.QueryOutput)
	links := make([]Link, 0, len(res.Items))
	for _, it := range res.Items {
		// GSI1 projects only longUrl and active. Everything else is reconstructed
		// from the key attributes, which an index always carries — that is what
		// keeps the projection (and so the index write cost) minimal.
		created, _ := time.Parse(time.RFC3339Nano, trimPrefix(str(it["GSI1SK"]), prefixCreat))
		links = append(links, Link{
			Code:      trimPrefix(str(it["PK"]), prefixLink),
			LongURL:   str(it["longUrl"]),
			OwnerID:   owner,
			CreatedAt: created,
			Active:    boolean(it["active"]),
		})
	}
	return links, nil
}

// ---------- readiness ----------

// Ping is what /readyz calls. DescribeTable is the cheapest call that proves both
// reachability and that the IAM policy still permits this table — which is exactly what
// chaos experiment 3 revokes.
func (d *Dynamo) Ping(ctx context.Context) error {
	_, err := d.call(ctx, "DescribeTable", func() (any, error) {
		return d.c.DescribeTable(ctx, &dynamodb.DescribeTableInput{
			TableName: aws.String(d.table),
		})
	})
	return err
}

// ---------- instrumentation ----------

// call wraps every DynamoDB request so latency, errors and throttles are recorded in
// exactly one place. The throttle counter here is what chaos experiment 1 asserts on
// alongside the CloudWatch metric.
func (d *Dynamo) call(_ context.Context, op string, fn func() (any, error)) (any, error) {
	start := time.Now()
	out, err := fn()
	elapsed := time.Since(start)

	result := "ok"
	switch {
	case err == nil:
	case IsThrottle(err):
		result = "throttled"
		metrics.DynamoThrottles.WithLabelValues(op).Inc()
	case isConditionalFailure(err):
		// Losing a conditional put is expected control flow, not a fault. Counting
		// it as an error would put noise in the error-rate panel the chaos
		// assertions read.
		result = "conditional_failed"
	default:
		result = "error"
	}

	metrics.DynamoRequests.WithLabelValues(op, result).Inc()
	metrics.DynamoDuration.WithLabelValues(op).Observe(elapsed.Seconds())
	return out, err
}

// IsThrottle reports whether err is DynamoDB refusing work for capacity reasons. Both
// the typed exception and the generic API code are checked: LocalStack and real DynamoDB
// do not always surface the same one.
func IsThrottle(err error) bool {
	if err == nil {
		return false
	}
	var pte *ddbtypes.ProvisionedThroughputExceededException
	if errors.As(err, &pte) {
		return true
	}
	var rle *ddbtypes.RequestLimitExceeded
	if errors.As(err, &rle) {
		return true
	}
	var ae smithy.APIError
	if errors.As(err, &ae) {
		switch ae.ErrorCode() {
		case "ProvisionedThroughputExceededException",
			"ThrottlingException",
			"RequestLimitExceeded",
			"TooManyRequestsException":
			return true
		}
	}
	return false
}

func isConditionalFailure(err error) bool {
	var cf *ddbtypes.ConditionalCheckFailedException
	return errors.As(err, &cf)
}

// ---------- attribute helpers ----------

func s(v string) ddbtypes.AttributeValue {
	return &ddbtypes.AttributeValueMemberS{Value: v}
}

func n(v int64) ddbtypes.AttributeValue {
	return &ddbtypes.AttributeValueMemberN{Value: strconv.FormatInt(v, 10)}
}

func str(av ddbtypes.AttributeValue) string {
	if m, ok := av.(*ddbtypes.AttributeValueMemberS); ok {
		return m.Value
	}
	return ""
}

func num(av ddbtypes.AttributeValue) int64 {
	if m, ok := av.(*ddbtypes.AttributeValueMemberN); ok {
		v, err := strconv.ParseInt(m.Value, 10, 64)
		if err == nil {
			return v
		}
	}
	return 0
}

func boolean(av ddbtypes.AttributeValue) bool {
	if m, ok := av.(*ddbtypes.AttributeValueMemberBOOL); ok {
		return m.Value
	}
	return false
}

func linkFromItem(it map[string]ddbtypes.AttributeValue) Link {
	created, _ := time.Parse(time.RFC3339Nano, str(it["createdAt"]))
	return Link{
		Code:      str(it["code"]),
		LongURL:   str(it["longUrl"]),
		OwnerID:   str(it["ownerId"]),
		CreatedAt: created,
		Active:    boolean(it["active"]),
	}
}
