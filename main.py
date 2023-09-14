import yfinance as yf
from flask import Flask, render_template, redirect, url_for, request, flash, abort
from flask_bootstrap import Bootstrap
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired
import plotly.graph_objects as go
import datetime as dt
import os

from linebot.v3 import (
    WebhookHandler
)
from linebot.v3.exceptions import (
    InvalidSignatureError
)
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent
)


app = Flask(__name__)

configuration = Configuration(access_token=os.environ.get('LINE_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

app.config['SECRET_KEY'] = os.environ.get('app_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
Bootstrap(app)
db = SQLAlchemy(app)


class SearchRecord(db.Model):
    __tablename__ = 'search_records'
    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String, nullable=False, unique=True)


def is_valid_date_format(date_string):
    try:
        dt.datetime.strptime(date_string, '%Y-%m-%d')
        return True
    except ValueError:
        return False


class SearchForm(FlaskForm):
    ticker = StringField('請輸入股票代號，台股上市需加.TW、上櫃需加.TWO，e.g.2330.TW', validators=[DataRequired()])
    begin_date = StringField('起始日期，日期格式須為yyyy-mm-dd，e.g.2023-01-01', validators=[DataRequired()])
    end_date = StringField('結束日期，日期格式須為yyyy-mm-dd，e.g.2023-03-01', validators=[DataRequired()])
    submit = SubmitField('搜尋')


with app.app_context():
    db.create_all()


@app.route('/', methods=['GET', 'POST'])
def home():
    form = SearchForm()
    search_records = db.session.query(SearchRecord).all()
    if form.validate_on_submit():
        searched_ticker = form.ticker.data
        #判斷股票代號是否符合格式
        if '.TW' in searched_ticker or '.TWO' in searched_ticker:
            begin_date = form.begin_date.data
            end_date = form.end_date.data
            #驗證日期格式是否正確
            if not is_valid_date_format(begin_date) or not is_valid_date_format(end_date):
                flash('錯誤的日期格式，請依照yyyy-mm-dd格式輸入日期')
                return redirect(url_for('home'))
            elif dt.datetime.strptime(begin_date, '%Y-%m-%d') >= dt.datetime.strptime(end_date, '%Y-%m-%d'):
                flash('起始日期必須小於結束日期，請重新輸入')
                return redirect(url_for('home'))
            else:
                return redirect(url_for('search_price',
                                        ticker=searched_ticker,
                                        begin_date=begin_date,
                                        end_date=end_date))
        else:
            # 使用redirect來清除輸入錯誤的內容
            flash('錯誤的股票代碼格式，上市股票須加.TW、上櫃股票須加.TWO')
            return redirect(url_for('home'))
    return render_template('index.html', form=form, records=search_records)


@app.route('/price/<string:ticker>')
def search_price(ticker):
    begin_date = request.args.get('begin_date')
    end_date = request.args.get('end_date')
    #如果從搜尋紀錄連結搜尋，則搜尋前三個月股價
    if begin_date is None or end_date is None:
        begin_date = str(dt.datetime.now().date() - dt.timedelta(90))
        end_date = str(dt.datetime.now().date())
    stock_price = yf.download(ticker, start=begin_date, end=end_date)

    #驗證是否有找到ticker
    if stock_price.empty:
        flash('查無此股票代碼，請確認後再輸入')
        return redirect(url_for('home'))
    else:
        ticker_record = SearchRecord.query.filter_by(ticker=ticker).first()
        # 儲存第一次查詢的ticker
        if not ticker_record:
            db.session.add(SearchRecord(ticker=ticker))
            db.session.commit()

        #計算移動平均值
        stock_price['5-day MA'] = stock_price['Close'].rolling(window=5).mean()
        stock_price['20-day MA'] = stock_price['Close'].rolling(window=20).mean()
        #將datetime格式轉為string避免x-axis顯示time
        stock_price.index = stock_price.index.astype(str)
        stock_price_candlestick = go.Candlestick(x=stock_price.index,
                                                 open=stock_price['Open'],
                                                 close=stock_price['Close'],
                                                 high=stock_price['High'],
                                                 low=stock_price['Low'],
                                                 showlegend=False,
                                                 increasing_line_color='red',
                                                 decreasing_line_color='green')

        five_day_ma_line = go.Scatter(x=stock_price.index,
                                      y=stock_price['5-day MA'],
                                      mode='lines',
                                      line=dict(color='blue'),
                                      name='5-day MA')
        twenty_day_ma_line = go.Scatter(x=stock_price.index,
                                        y=stock_price['20-day MA'],
                                        mode='lines',
                                        line=dict(color='orange'),
                                        name='20-day MA')
        fig = go.Figure(data=[stock_price_candlestick, five_day_ma_line, twenty_day_ma_line])
        #設定xaxis_type為'category'來避免顯示非交易日
        fig.update_layout(title=ticker,
                          xaxis_type='category',
                          xaxis_rangeslider_visible=False)
        chart_json = fig.to_json()
        return render_template('price.html', chart_json=chart_json, chart_id='chart')


@app.route('/delete')
def delete_record():
    deleted_ticker = SearchRecord.query.filter_by(ticker=request.args.get('ticker')).first()
    if deleted_ticker:
        db.session.delete(deleted_ticker)
        db.session.commit()
    return redirect(url_for('home'))


#line bot

@app.route("/callback", methods=['POST'])
def callback():
    # get X-Line-Signature header value
    signature = request.headers['X-Line-Signature']

    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.info("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)

    return 'OK'


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=event.message.text)]
            )
        )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)